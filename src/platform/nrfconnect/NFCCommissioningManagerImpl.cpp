/*
 *
 *    Copyright (c) 2026 Project CHIP Authors
 *
 *    Licensed under the Apache License, Version 2.0 (the "License");
 *    you may not use this file except in compliance with the License.
 *    You may obtain a copy of the License at
 *
 *        http://www.apache.org/licenses/LICENSE-2.0
 *
 *    Unless required by applicable law or agreed to in writing, software
 *    distributed under the License is distributed on an "AS IS" BASIS,
 *    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *    See the License for the specific language governing permissions and
 *    limitations under the License.
 */

#include <platform/internal/CHIPDeviceLayerInternal.h>
#include <platform/internal/NFCCommissioningManager.h>

#include <app/server/Server.h> // nogncheck
#include <platform/nrfconnect/NFCCommissioningManagerImpl.h>

#include <lib/support/CHIPMem.h>
#include <lib/support/CodeUtils.h>
#include <lib/support/SafeInt.h>
#include <lib/support/StringBuilder.h>
#include <platform/CommissionableDataProvider.h>
#include <platform/DeviceInstanceInfoProvider.h>
#include <platform/PlatformManager.h>
#include <setup_payload/QRCodeSetupPayloadGenerator.h>
#include <setup_payload/SetupPayload.h>

#include <nfc/ndef/uri_msg.h>
#include <nfc/ndef/uri_rec.h>

#include <algorithm>
#include <cstring>

#include <zephyr/kernel.h>

using namespace ::chip::Nfc;

namespace chip {
namespace DeviceLayer {
namespace Internal {

namespace {

// Matter Application Identifier (NTL, spec 4.21)
constexpr uint8_t kMatterAid[] = { 0xA0, 0x00, 0x00, 0x09, 0x09, 0x8A, 0x77, 0xE4, 0x01 };

// NDEF Tag Application Identifier (NFC Forum Type 4 Tag operation specification)
constexpr uint8_t kNdefAid[] = { 0xD2, 0x76, 0x00, 0x00, 0x85, 0x01, 0x01 };

// File IDs used within the NDEF Tag Application
constexpr uint8_t kCapabilityContainerFileId[] = { 0xE1, 0x03 };
constexpr uint8_t kNdefFileId[]                = { 0xE1, 0x04 };

// Capability Container (CC) file contents, describing the NDEF file to a reader/writer. MLe/MLc
// are deliberately conservative (matching common NFC Forum Type 4 Tag reference values) rather
// than the largest size we could technically support.
constexpr uint8_t kCapabilityContainer[] = {
    0x00,
    0x0F, // CCLEN: 15 bytes
    0x20, // Mapping Version 2.0
    0x00,
    0x3B, // MLe: maximum R-APDU data size for READ BINARY
    0x00,
    0x34, // MLc: maximum C-APDU data size for UPDATE BINARY (unused: no write support)
    0x04,
    0x06, // NDEF File Control TLV: Type, Length
    kNdefFileId[0],
    kNdefFileId[1],
    static_cast<uint8_t>((NFCCommissioningManagerImpl::kNdefFileBufferSize) >> 8),
    static_cast<uint8_t>((NFCCommissioningManagerImpl::kNdefFileBufferSize) & 0xFF), // Max NDEF file size
    0x00,                                                                            // Read access granted
    0xFF,                                                                            // Write access denied
};

// ISO/IEC 7816-4 status words used below
constexpr uint8_t kSwSuccess1                 = 0x90;
constexpr uint8_t kSwSuccess2                 = 0x00;
constexpr uint8_t kSwMoreData1                = 0x61; // remaining byte count in SW2
constexpr uint8_t kSwConditionsNotSatisfied1  = 0x69;
constexpr uint8_t kSwConditionsNotSatisfied2  = 0x85;
constexpr uint8_t kSwNotEnoughMemory1         = 0x6A;
constexpr uint8_t kSwNotEnoughMemory2         = 0x84;
constexpr uint8_t kSwWrongParameters1         = 0x6A;
constexpr uint8_t kSwWrongParameters2         = 0x86;
constexpr uint8_t kSwFileOrAppletNotFound1    = 0x6A;
constexpr uint8_t kSwFileOrAppletNotFound2    = 0x82;
constexpr uint8_t kSwWrongLength1             = 0x67;
constexpr uint8_t kSwWrongLength2             = 0x00;
constexpr uint8_t kSwClassNotSupported1       = 0x6E;
constexpr uint8_t kSwClassNotSupported2       = 0x00;
constexpr uint8_t kSwInstructionNotSupported1 = 0x6D;
constexpr uint8_t kSwInstructionNotSupported2 = 0x00;

// ISO/IEC 7816-4 instruction codes used by the NTL APDU layer
constexpr uint8_t kInsSelect      = 0xA4;
constexpr uint8_t kInsReadBinary  = 0xB0;
constexpr uint8_t kInsTransport   = 0x20;
constexpr uint8_t kInsGetResponse = 0xC0;

// TRANSPORT command CLA byte: bit 0x10 is the ISO/IEC 7816-4 command-chaining bit.
constexpr uint8_t kClaTransportUnchained = 0x80;
constexpr uint8_t kClaTransportChained   = 0x90;
constexpr uint8_t kClaChainingBit        = 0x10;

// ISO/IEC 7816-4 short-form Le coding: a value of 0 means "256 bytes expected".
size_t DecodeShortLe(uint8_t leByte)
{
    return (leByte == 0) ? 256 : leByte;
}

constexpr uint8_t kSecureSessionEstablishedTransportNfc = 5; // Transport::Type::kNfc
constexpr uint8_t kSecureSessionEstablishedSessionPase  = 1; // Transport::SecureSession::Type::kPASE

#ifdef CHIP_NFC_BASED_COMMISSIONING_APDU_DEBUG
void LogApduHex(const char * label, const uint8_t * data, size_t dataLength)
{
    constexpr size_t kMaxLoggedBytes = 32;
    // Up to 3 chars per byte (" XX"), plus room for a truncation suffix and NUL.
    StringBuilder<3 * kMaxLoggedBytes + 16> builder;
    size_t loggedLength = std::min(dataLength, kMaxLoggedBytes);
    for (size_t i = 0; i < loggedLength; ++i)
    {
        builder.Add(i == 0 ? "" : " ").AddFormat("%02X", data[i]);
    }
    if (dataLength > loggedLength)
    {
        builder.Add(" ...");
    }
    ChipLogProgress(DeviceLayer, "%s (%u bytes): %s", label, static_cast<unsigned int>(dataLength), builder.c_str());
}
#endif // CHIP_NFC_BASED_COMMISSIONING_APDU_DEBUG

K_MUTEX_DEFINE(sSessionMutex);

class SessionLock
{
public:
    SessionLock() { k_mutex_lock(&sSessionMutex, K_FOREVER); }
    ~SessionLock() { k_mutex_unlock(&sSessionMutex); }

    SessionLock(const SessionLock &)             = delete;
    SessionLock & operator=(const SessionLock &) = delete;
};

} // namespace

Global<NFCCommissioningManagerImpl> NFCCommissioningManagerImpl::sInstance;

CHIP_ERROR NFCCommissioningManagerImpl::_Init()
{
    ChipLogDetail(DeviceLayer, "Initializing NFC Commissioning Manager");

    ResetSession();
    ReturnErrorOnFailure(PlatformMgr().AddEventHandler(OnPlatformEvent, reinterpret_cast<intptr_t>(this)));

    return CHIP_NO_ERROR;
}

void NFCCommissioningManagerImpl::OnPlatformEvent(const ChipDeviceEvent * event, intptr_t arg)
{
    auto * self = reinterpret_cast<NFCCommissioningManagerImpl *>(arg);

    switch (event->Type)
    {
    case DeviceEventType::kServerReady:
    case DeviceEventType::kDnssdInitialized:
        self->StartNfcCommissioning();
        break;
    case DeviceEventType::kCommissioningComplete:
        self->HandleCommissioningComplete();
        break;
    case DeviceEventType::kFailSafeTimerExpired:
        self->HandleFailSafeTimerExpired();
        break;
    case DeviceEventType::kSecureSessionEstablished:
        self->HandleSecureSessionEstablished(event);
        break;
    default:
        break;
    }
}

void NFCCommissioningManagerImpl::ScheduledStartNfcCommissioning(intptr_t arg)
{
    auto * self = reinterpret_cast<NFCCommissioningManagerImpl *>(arg);
    VerifyOrReturn(self != nullptr);
    self->StartNfcCommissioning();
}

void NFCCommissioningManagerImpl::StartNfcCommissioning()
{
    VerifyOrReturn(!mRawIsoDepStarted);

    if (Server::GetInstance().GetFabricTable().FabricCount() != 0)
    {
        ChipLogProgress(DeviceLayer, "Device already commissioned: NFC commissioning will stay disabled");
        return;
    }

    StartRawIsoDepTagEmulation();
    if (!mRawIsoDepStarted)
    {
        ChipLogError(DeviceLayer, "Failed to start NFC tag emulation for commissioning");
    }
}

void NFCCommissioningManagerImpl::HandleCommissioningComplete()
{
    mBlockMatterAidSelection        = false;
    mNfcEmulationPausedForFailSafe  = false;
    ChipLogProgress(DeviceLayer, "Commissioning complete: stopping NFC commissioning");
    NFCCommissioningMgr().Shutdown();
}

void NFCCommissioningManagerImpl::HandleFailSafeTimerExpired()
{
    VerifyOrReturn(mBlockMatterAidSelection || mNfcEmulationPausedForFailSafe);

    mBlockMatterAidSelection       = false;
    mNfcEmulationPausedForFailSafe = false;

    if (Server::GetInstance().GetFabricTable().FabricCount() != 0)
    {
        return;
    }

    TEMPORARY_RETURN_IGNORED ConfigureOnboardingPayload();
    ChipLogProgress(DeviceLayer, "Fail-safe expired: NFC commissioning is available again");
}

void NFCCommissioningManagerImpl::HandleSecureSessionEstablished(const ChipDeviceEvent * event)
{
    VerifyOrReturn(event != nullptr);

    const auto & sessionEstablished = event->SecureSessionEstablished;
    if (sessionEstablished.TransportType != kSecureSessionEstablishedTransportNfc)
    {
        return;
    }

    if (sessionEstablished.SecureSessionType != kSecureSessionEstablishedSessionPase)
    {
        return;
    }

    mBlockMatterAidSelection = true;
    TEMPORARY_RETURN_IGNORED ClearOnboardingPayload();
    ChipLogProgress(DeviceLayer,
                    "NFC PASE session established: blocking NFC commissioning until fail-safe completes or commissioning succeeds");
}

CHIP_ERROR NFCCommissioningManagerImpl::ConfigureOnboardingPayload()
{
    PayloadContents payload;
    payload.version = 0;
    payload.rendezvousInformation.SetValue(RendezvousInformationFlags(RendezvousInformationFlag::kNFC));

    ReturnErrorOnFailure(GetCommissionableDataProvider()->GetSetupPasscode(payload.setUpPINCode));

    uint16_t discriminator = 0;
    ReturnErrorOnFailure(GetCommissionableDataProvider()->GetSetupDiscriminator(discriminator));
    payload.discriminator.SetLongValue(discriminator);

    ReturnErrorOnFailure(GetDeviceInstanceInfoProvider()->GetVendorId(payload.vendorID));
    ReturnErrorOnFailure(GetDeviceInstanceInfoProvider()->GetProductId(payload.productID));

    char qrCodeBuffer[QRCodeBasicSetupPayloadGenerator::kMaxQRCodeBase38RepresentationLength + 1];
    MutableCharSpan qrCode(qrCodeBuffer);
    ReturnErrorOnFailure(QRCodeBasicSetupPayloadGenerator(payload).payloadBase38Representation(qrCode));

    ReturnErrorOnFailure(SetOnboardingPayload(qrCode.data(), qrCode.size()));

    // ConfigureOnboardingPayload() runs before Server::Init(); defer NFC start until the fabric
    // table is loaded. kServerReady is not reliable on all networking paths (e.g. Wi-Fi builds
    // without an IPv6 mDNS listener at boot), so schedule an explicit start attempt as well.
    TEMPORARY_RETURN_IGNORED PlatformMgr().ScheduleWork(ScheduledStartNfcCommissioning, reinterpret_cast<intptr_t>(this));

    return CHIP_NO_ERROR;
}

void NFCCommissioningManagerImpl::StartRawIsoDepTagEmulation()
{
    VerifyOrReturn(!mRawIsoDepStarted);

    int err = nfc_t4t_setup(OnT4TEvent, this);
    if (err)
    {
        ChipLogError(DeviceLayer, "nfc_t4t_setup() failed: %d", err);
        return;
    }

    err = nfc_t4t_emulation_start();
    if (err)
    {
        ChipLogError(DeviceLayer, "nfc_t4t_emulation_start() failed: %d", err);
        nfc_t4t_done();
        return;
    }

    mRawIsoDepStarted = true;
    ChipLogProgress(DeviceLayer, "NFC NDEF Tag emulation started");
}

void NFCCommissioningManagerImpl::StopRawIsoDepTagEmulation()
{
    VerifyOrReturn(mRawIsoDepStarted);

    nfc_t4t_emulation_stop();
    nfc_t4t_done();
    mRawIsoDepStarted = false;
}

void NFCCommissioningManagerImpl::PauseNfcTagEmulationForFailSafe()
{
    VerifyOrReturn(!mNfcEmulationPausedForFailSafe);

    mNfcEmulationPausedForFailSafe = true;
    StopRawIsoDepTagEmulation();
    ChipLogProgress(DeviceLayer, "NFC tag emulation paused until fail-safe completes");
}

bool NFCCommissioningManagerImpl::IsSessionIdle() const
{
    return mSelectedApplication == SelectedApplication::kNone && !mOutgoingContinuationPending && mApduLength == 0 &&
        mApduFragmentCount == 0 && !mAwaitingApplicationResponse && mOutgoingMessage.IsNull();
}

void NFCCommissioningManagerImpl::_Shutdown()
{
    ChipLogDetail(DeviceLayer, "Shutting down NFC Commissioning Manager");
    PlatformMgr().RemoveEventHandler(OnPlatformEvent, reinterpret_cast<intptr_t>(this));
    StopRawIsoDepTagEmulation();
    ResetSession(/* notifyAborted = */ true);
}

void NFCCommissioningManagerImpl::ResetSession(bool notifyAborted)
{
    SessionLock lock;
    ResetSessionState(notifyAborted);
}

void NFCCommissioningManagerImpl::ScheduleNfcTagError(Transport::NFCBase * nfcBase, const Transport::PeerAddress & peerAddress)
{
    struct Context
    {
        Transport::NFCBase * nfcBase;
        Transport::PeerAddress peerAddress;
    };

    auto * context = Platform::New<Context>(Context{ nfcBase, peerAddress });
    VerifyOrReturn(context != nullptr);

    CHIP_ERROR err = PlatformMgr().ScheduleWork(
        [](intptr_t arg) {
            auto * ctx = reinterpret_cast<Context *>(arg);
            if (ctx->nfcBase != nullptr)
            {
                ctx->nfcBase->OnNfcTagError(ctx->peerAddress);
            }
            Platform::Delete(ctx);
        },
        reinterpret_cast<intptr_t>(context));

    if (err != CHIP_NO_ERROR)
    {
        ChipLogError(DeviceLayer, "Failed to schedule NFC tag error notification: %" CHIP_ERROR_FORMAT, err.Format());
        Platform::Delete(context);
    }
}

void NFCCommissioningManagerImpl::ResetSessionState(bool notifyAborted)
{
    if (notifyAborted && mNFCBase != nullptr && mSelectedApplication == SelectedApplication::kMatter)
    {
        ScheduleNfcTagError(mNFCBase, mPeerAddress);
    }

    mSelectedApplication         = SelectedApplication::kNone;
    mSelectedFile                = SelectedFile::kNone;
    mPendingResponseLe           = 0;
    mOutgoingContinuationPending = false;
    mApduLength                  = 0;
    mApduFragmentCount           = 0;
    ResetIncomingMessage();
    mOutgoingMessage             = nullptr;
    mOutgoingOffset              = 0;
    mAwaitingApplicationResponse = false;
    mPeerAddress                 = Transport::PeerAddress::NFC();
}

void NFCCommissioningManagerImpl::ResetIncomingMessage()
{
    mIncomingMessage        = nullptr;
    mIncomingExpectedLength = 0;
    mIncomingReceivedLength = 0;
}

CHIP_ERROR NFCCommissioningManagerImpl::SetOnboardingPayload(const char * payload, size_t payloadLength)
{
    SessionLock lock;

    VerifyOrReturnError(CanCastTo<uint8_t>(payloadLength), CHIP_ERROR_BUFFER_TOO_SMALL);

    uint32_t ndefLength = kNdefMessageCapacity;
    int result          = nfc_ndef_uri_msg_encode(NFC_URI_NONE, reinterpret_cast<const uint8_t *>(payload),
                                                  static_cast<uint8_t>(payloadLength), &mNdefFileBuffer[2], &ndefLength);
    if (result)
    {
        ChipLogError(DeviceLayer, "nfc_ndef_uri_msg_encode() failed: %d", result);
        return CHIP_ERROR_BUFFER_TOO_SMALL;
    }

    mNdefFileBuffer[0]    = static_cast<uint8_t>(ndefLength >> 8);
    mNdefFileBuffer[1]    = static_cast<uint8_t>(ndefLength & 0xFF);
    mNdefFileLength       = 2 + ndefLength;
    mHasOnboardingPayload = true;

    ChipLogDetail(DeviceLayer, "NFC onboarding payload set");
    return CHIP_NO_ERROR;
}

CHIP_ERROR NFCCommissioningManagerImpl::ClearOnboardingPayload()
{
    SessionLock lock;

    mNdefFileBuffer[0]    = 0;
    mNdefFileBuffer[1]    = 0;
    mNdefFileLength       = 2;
    mHasOnboardingPayload = false;
    ChipLogDetail(DeviceLayer, "NFC onboarding payload cleared");
    return CHIP_NO_ERROR;
}

// ===== start implement virtual methods on NfcApplicationDelegate.

void NFCCommissioningManagerImpl::SetNFCBase(Transport::NFCBase * nfcBase)
{
    SessionLock lock;
    mNFCBase = nfcBase;
}

bool NFCCommissioningManagerImpl::CanSendToPeer(const Transport::PeerAddress & address)
{
    SessionLock lock;
    return mSelectedApplication == SelectedApplication::kMatter && address.GetTransportType() == Transport::Type::kNfc &&
        address == mPeerAddress;
}

CHIP_ERROR NFCCommissioningManagerImpl::SendToNfcTag(const Transport::PeerAddress & address, System::PacketBufferHandle && msgBuf)
{
    VerifyOrReturnError(address.GetTransportType() == Transport::Type::kNfc, CHIP_ERROR_INVALID_ARGUMENT);
    VerifyOrReturnError(!msgBuf.IsNull(), CHIP_ERROR_INVALID_ARGUMENT);

    SessionLock lock;

    VerifyOrReturnError(address == mPeerAddress, CHIP_ERROR_INVALID_ARGUMENT);
    VerifyOrReturnError(mSelectedApplication == SelectedApplication::kMatter, CHIP_ERROR_INCORRECT_STATE);
    VerifyOrReturnError(!mOutgoingContinuationPending, CHIP_ERROR_INCORRECT_STATE);
    VerifyOrReturnError(mOutgoingMessage.IsNull(), CHIP_ERROR_INCORRECT_STATE);

    ChipLogDetail(DeviceLayer, "Sending message of length %lu bytes to NFC Reader/Writer",
                  static_cast<unsigned long>(msgBuf->DataLength()));

    mOutgoingMessage             = std::move(msgBuf);
    mOutgoingOffset              = 0;
    mOutgoingContinuationPending = false;

    SendOutgoingMessageChunk(mPendingResponseLe);
    return CHIP_NO_ERROR;
}

// ===== nfc_t4t_lib raw ISO-DEP callback

void NFCCommissioningManagerImpl::OnT4TEvent(void * context, nfc_t4t_event_t event, const uint8_t * data, size_t dataLength,
                                             uint32_t flags)
{
    auto * self = static_cast<NFCCommissioningManagerImpl *>(context);
    if (self == nullptr)
    {
        return;
    }

    if (self->mNfcEmulationPausedForFailSafe)
    {
        return;
    }

    switch (event)
    {
    case NFC_T4T_EVENT_FIELD_ON:
        self->HandleFieldOn();
        break;
    case NFC_T4T_EVENT_FIELD_OFF:
        self->HandleFieldOff();
        break;
    case NFC_T4T_EVENT_DATA_IND:
        self->HandleDataIndication(data, dataLength, flags);
        break;
    case NFC_T4T_EVENT_DATA_TRANSMITTED:
        self->HandleDataTransmitted();
        break;
    default:
        break;
    }
}

void NFCCommissioningManagerImpl::HandleFieldOn()
{
    SessionLock lock;

    if (mNfcEmulationPausedForFailSafe)
    {
        ChipLogDetail(DeviceLayer, "NFC field detected while fail-safe pause is active: ignoring");
        return;
    }

    if (mBlockMatterAidSelection)
    {
        ChipLogProgress(DeviceLayer, "NFC field detected while fail-safe is active: pausing tag emulation");
        ResetSessionState();
        PauseNfcTagEmulationForFailSafe();
        return;
    }

    ChipLogDetail(DeviceLayer, "NFC field detected: an NFC Reader/Writer is polling");
    // A field-on event always precedes a fresh ISO-DEP activation: start with a clean slate.
    ResetSessionState();
}

void NFCCommissioningManagerImpl::HandleFieldOff()
{
    SessionLock lock;

    if (IsSessionIdle())
    {
        if (mNfcEmulationPausedForFailSafe || !mBlockMatterAidSelection)
        {
            return;
        }
    }

    ChipLogDetail(DeviceLayer, "NFC field lost: the NFC Reader/Writer moved away");
    ResetSessionState(/* notifyAborted = */ true);

    if (mBlockMatterAidSelection && mRawIsoDepStarted)
    {
        PauseNfcTagEmulationForFailSafe();
    }
}

void NFCCommissioningManagerImpl::HandleDataTransmitted()
{
    ChipLogDetail(DeviceLayer, "NFC Response APDU transmitted");
}

void NFCCommissioningManagerImpl::HandleDataIndication(const uint8_t * data, size_t dataLength, uint32_t flags)
{
    SessionLock lock;

    if (mApduLength + dataLength > sizeof(mApduBuffer))
    {
        ChipLogError(DeviceLayer, "Incoming APDU exceeds maximum size of %u bytes, dropping it",
                     static_cast<unsigned int>(sizeof(mApduBuffer)));
        mApduLength        = 0;
        mApduFragmentCount = 0;
        SendStatusResponse(kSwWrongLength1, kSwWrongLength2);
        return;
    }

    memcpy(&mApduBuffer[mApduLength], data, dataLength);
    mApduLength += dataLength;
    ++mApduFragmentCount;

    if (flags & NFC_T4T_DI_FLAG_MORE)
    {
        ChipLogDetail(DeviceLayer, "NFC DATA_IND fragment #%u: %u byte(s), MORE data expected, running total %u byte(s)",
                      static_cast<unsigned int>(mApduFragmentCount), static_cast<unsigned int>(dataLength),
                      static_cast<unsigned int>(mApduLength));
        return;
    }

    size_t apduLength    = mApduLength;
    size_t fragmentCount = mApduFragmentCount;
    mApduLength          = 0;
    mApduFragmentCount   = 0;
    ProcessCommandApdu(mApduBuffer, apduLength, fragmentCount);
}

// ===== APDU layer

void NFCCommissioningManagerImpl::ProcessCommandApdu(const uint8_t * apdu, size_t apduLength, size_t fragmentCount)
{
    constexpr size_t kMaxSnapshotBytes = 32;
    uint8_t snapshot[kMaxSnapshotBytes];
    size_t snapshotLength = std::min(apduLength, kMaxSnapshotBytes);
    memcpy(snapshot, apdu, snapshotLength);

    if (apduLength < 4)
    {
        SendStatusResponse(kSwWrongParameters1, kSwWrongParameters2);
#ifdef CHIP_NFC_BASED_COMMISSIONING_APDU_DEBUG
        LogApduHex("Received command APDU (too short)", snapshot, snapshotLength);
#endif // CHIP_NFC_BASED_COMMISSIONING_APDU_DEBUG
        return;
    }

    switch (apdu[1] /* INS */)
    {
    case kInsSelect:
        HandleSelectCommand(apdu, apduLength);
        break;
    case kInsReadBinary:
        HandleReadBinaryCommand(apdu, apduLength);
        break;
    case kInsTransport:
        HandleTransportCommand(apdu, apduLength);
        break;
    case kInsGetResponse:
        HandleGetResponseCommand(apdu, apduLength);
        break;
    default:
        SendStatusResponse(kSwInstructionNotSupported1, kSwInstructionNotSupported2);
        break;
    }

    ChipLogDetail(DeviceLayer, "Reassembled from %u ISO-DEP fragment(s)", static_cast<unsigned int>(fragmentCount));

#ifdef CHIP_NFC_BASED_COMMISSIONING_APDU_DEBUG
    LogApduHex("Received command APDU", snapshot, snapshotLength);
#endif // CHIP_NFC_BASED_COMMISSIONING_APDU_DEBUG
}

void NFCCommissioningManagerImpl::HandleSelectCommand(const uint8_t * apdu, size_t apduLength)
{
    // SELECT command layout: CLA INS P1 P2 Lc Data [Le]
    if (apduLength < 5 || apdu[0] != 0x00)
    {
        SendStatusResponse(kSwWrongParameters1, kSwWrongParameters2);
        return;
    }

    uint8_t p1 = apdu[2];
    uint8_t p2 = apdu[3];
    uint8_t lc = apdu[4];

    if (apduLength != static_cast<size_t>(5 + lc) && apduLength != static_cast<size_t>(5 + lc + 1))
    {
        SendStatusResponse(kSwWrongParameters1, kSwWrongParameters2);
        return;
    }

    const uint8_t * data = &apdu[5];

    // SELECT by name (DF/application name), e.g. the Matter AID or the NDEF Tag Application AID.
    if (p1 == 0x04 && (p2 == 0x00 || p2 == 0x0C))
    {
        HandleSelectByNameCommand(data, lc);
        return;
    }

    // SELECT by file ID, for an elementary file (CC or NDEF file) under the currently selected
    // application.
    if (p1 == 0x00 && (p2 == 0x00 || p2 == 0x0C) && lc == 2)
    {
        HandleSelectByFileIdCommand(data);
        return;
    }

    RejectUnsupportedSelect(p1, p2);
}

void NFCCommissioningManagerImpl::RejectUnsupportedSelect(uint8_t p1, uint8_t p2)
{
    mSelectedApplication = SelectedApplication::kNone;
    mSelectedFile        = SelectedFile::kNone;
    SendStatusResponse(kSwFileOrAppletNotFound1, kSwFileOrAppletNotFound2);
    ChipLogProgress(DeviceLayer, "Ignoring unsupported SELECT command (P1=0x%02X, P2=0x%02X)", p1, p2);
}

void NFCCommissioningManagerImpl::HandleSelectByNameCommand(const uint8_t * aid, size_t aidLength)
{
    mSelectedFile = SelectedFile::kNone;

    if (aidLength == sizeof(kMatterAid) && memcmp(aid, kMatterAid, sizeof(kMatterAid)) == 0)
    {
        HandleMatterAidSelected();
        return;
    }

    if (aidLength == sizeof(kNdefAid) && memcmp(aid, kNdefAid, sizeof(kNdefAid)) == 0)
    {
        if (mBlockMatterAidSelection)
        {
            mSelectedApplication = SelectedApplication::kNone;
            mSelectedFile        = SelectedFile::kNone;
            SendStatusResponse(kSwConditionsNotSatisfied1, kSwConditionsNotSatisfied2);
            ChipLogProgress(DeviceLayer,
                            "Rejecting NDEF Tag Application selection: commissioning fail-safe is active, waiting for fail-safe "
                            "to complete");
            return;
        }

        mSelectedApplication = SelectedApplication::kNdef;
        SendStatusResponse(kSwSuccess1, kSwSuccess2);
        ChipLogProgress(DeviceLayer, "NDEF Tag Application selected");
        return;
    }

    mSelectedApplication = SelectedApplication::kNone;
    SendStatusResponse(kSwFileOrAppletNotFound1, kSwFileOrAppletNotFound2);
    ChipLogProgress(DeviceLayer, "Ignoring SELECT of unrecognized AID (%u byte(s))", static_cast<unsigned int>(aidLength));
}

void NFCCommissioningManagerImpl::HandleSelectByFileIdCommand(const uint8_t * fileId)
{
    if (mSelectedApplication != SelectedApplication::kNdef)
    {
        SendStatusResponse(kSwConditionsNotSatisfied1, kSwConditionsNotSatisfied2);
        ChipLogProgress(DeviceLayer, "Rejecting SELECT by file ID: the NDEF Tag Application has not been SELECTed yet");
        return;
    }

    if (memcmp(fileId, kCapabilityContainerFileId, sizeof(kCapabilityContainerFileId)) == 0)
    {
        mSelectedFile = SelectedFile::kCapabilityContainer;
    }
    else if (memcmp(fileId, kNdefFileId, sizeof(kNdefFileId)) == 0)
    {
        mSelectedFile = SelectedFile::kNdefFile;
    }
    else
    {
        mSelectedFile = SelectedFile::kNone;
        SendStatusResponse(kSwFileOrAppletNotFound1, kSwFileOrAppletNotFound2);
        ChipLogProgress(DeviceLayer, "Ignoring SELECT of unrecognized file ID");
        return;
    }

    SendStatusResponse(kSwSuccess1, kSwSuccess2);
}

void NFCCommissioningManagerImpl::HandleMatterAidSelected()
{
    if (mBlockMatterAidSelection)
    {
        mSelectedApplication = SelectedApplication::kNone;
        mSelectedFile        = SelectedFile::kNone;
        SendStatusResponse(kSwConditionsNotSatisfied1, kSwConditionsNotSatisfied2);
        ChipLogProgress(DeviceLayer,
                        "Rejecting Matter AID selection: commissioning fail-safe is active, waiting for fail-safe to complete");
        return;
    }

    mSelectedApplication = SelectedApplication::kMatter;

    uint16_t discriminator = 0;
    uint16_t vendorId      = 0;
    uint16_t productId     = 0;
    CHIP_ERROR err         = GetCommissionableDataProvider()->GetSetupDiscriminator(discriminator);
    if (err != CHIP_NO_ERROR)
    {
        ChipLogError(DeviceLayer, "Failed to get setup discriminator: %" CHIP_ERROR_FORMAT, err.Format());
    }
    err = GetDeviceInstanceInfoProvider()->GetVendorId(vendorId);
    if (err != CHIP_NO_ERROR)
    {
        ChipLogError(DeviceLayer, "Failed to get vendor ID: %" CHIP_ERROR_FORMAT, err.Format());
    }
    err = GetDeviceInstanceInfoProvider()->GetProductId(productId);
    if (err != CHIP_NO_ERROR)
    {
        ChipLogError(DeviceLayer, "Failed to get product ID: %" CHIP_ERROR_FORMAT, err.Format());
    }

    uint16_t formatAndDiscriminator = static_cast<uint16_t>(discriminator & 0x0FFF);
    mPeerAddress                    = Transport::PeerAddress::NFC(formatAndDiscriminator);

    uint8_t response[8];
    response[0] = kNtlProtocolVersion;
    response[1] = 0x00; // reserved
    response[2] = static_cast<uint8_t>(formatAndDiscriminator >> 8);
    response[3] = static_cast<uint8_t>(formatAndDiscriminator & 0xFF);
    response[4] = static_cast<uint8_t>(vendorId >> 8);
    response[5] = static_cast<uint8_t>(vendorId & 0xFF);
    response[6] = static_cast<uint8_t>(productId >> 8);
    response[7] = static_cast<uint8_t>(productId & 0xFF);

    SendDataResponse(response, sizeof(response), kSwSuccess1, kSwSuccess2);
    ChipLogProgress(DeviceLayer, "Matter AID selected: commissioning session started");
}

void NFCCommissioningManagerImpl::HandleReadBinaryCommand(const uint8_t * apdu, size_t apduLength)
{
    if (mBlockMatterAidSelection)
    {
        SendStatusResponse(kSwConditionsNotSatisfied1, kSwConditionsNotSatisfied2);
        ChipLogProgress(DeviceLayer,
                        "Rejecting READ BINARY: commissioning fail-safe is active, waiting for fail-safe to complete");
        return;
    }

    if (mSelectedApplication != SelectedApplication::kNdef || mSelectedFile == SelectedFile::kNone)
    {
        SendStatusResponse(kSwConditionsNotSatisfied1, kSwConditionsNotSatisfied2);
        ChipLogError(DeviceLayer, "Rejecting READ BINARY: no NDEF Tag Application file is currently selected");
        return;
    }

    // READ BINARY command layout: CLA INS P1 P2 Le, with a 15-bit big-endian file offset spread
    // across P1 (top bit clear) and P2.
    if (apduLength != 5 || (apdu[2] & 0x80) != 0)
    {
        SendStatusResponse(kSwWrongParameters1, kSwWrongParameters2);
        return;
    }

    size_t offset = (static_cast<size_t>(apdu[2]) << 8) | apdu[3];
    size_t le     = DecodeShortLe(apdu[4]);

    const uint8_t * fileData;
    size_t fileLength;
    if (mSelectedFile == SelectedFile::kCapabilityContainer)
    {
        fileData   = kCapabilityContainer;
        fileLength = sizeof(kCapabilityContainer);
    }
    else
    {
        fileData   = mNdefFileBuffer;
        fileLength = mNdefFileLength;
    }

    if (offset > fileLength)
    {
        SendStatusResponse(kSwWrongParameters1, kSwWrongParameters2);
        return;
    }

    size_t available = std::min(le, fileLength - offset);
    SendDataResponse(&fileData[offset], available, kSwSuccess1, kSwSuccess2);
}

void NFCCommissioningManagerImpl::HandleTransportCommand(const uint8_t * apdu, size_t apduLength)
{
    if (mSelectedApplication != SelectedApplication::kMatter)
    {
        SendStatusResponse(kSwConditionsNotSatisfied1, kSwConditionsNotSatisfied2);
        ChipLogError(DeviceLayer, "Rejecting TRANSPORT command: the Matter AID has not been SELECTed yet");
        return;
    }

    uint8_t cla = apdu[0];
    if (cla != kClaTransportUnchained && cla != kClaTransportChained)
    {
        SendStatusResponse(kSwClassNotSupported1, kSwClassNotSupported2);
        ChipLogError(DeviceLayer, "Rejecting TRANSPORT command: unsupported CLA byte 0x%02X", cla);
        return;
    }

    if (apduLength < 6)
    {
        SendStatusResponse(kSwWrongParameters1, kSwWrongParameters2);
        return;
    }

    uint16_t totalLength = static_cast<uint16_t>((apdu[2] << 8) | apdu[3]);
    uint8_t lc           = apdu[4];
    bool hasLe           = (apduLength == static_cast<size_t>(5 + lc + 1));

    if (apduLength != static_cast<size_t>(5 + lc) && !hasLe)
    {
        ChipLogError(DeviceLayer,
                     "TRANSPORT command length mismatch: got %u byte APDU but Lc=%u implies %u (Case 3, no Le) or %u "
                     "(Case 4, with Le) bytes; likely an ISO-DEP chaining/reassembly failure below the APDU layer",
                     static_cast<unsigned int>(apduLength), lc, static_cast<unsigned int>(5 + lc),
                     static_cast<unsigned int>(5 + lc + 1));
        SendStatusResponse(kSwNotEnoughMemory1, kSwNotEnoughMemory2);
        return;
    }

    const uint8_t * fragment = &apdu[5];
    size_t le                = hasLe ? DecodeShortLe(apdu[5 + lc]) : 256;
    bool chained             = (cla & kClaChainingBit) != 0;

    if (mAwaitingApplicationResponse)
    {
        if (!mOutgoingMessage.IsNull())
        {
            ChipLogDetail(DeviceLayer, "Serving pending outgoing message in response to a polling TRANSPORT command");
            SendOutgoingMessageChunk(le);
        }
        else
        {
            ChipLogDetail(DeviceLayer, "Acknowledging a polling TRANSPORT command: response not ready yet");
            SendStatusResponse(kSwSuccess1, kSwSuccess2);
        }
        return;
    }

    if (mIncomingMessage.IsNull())
    {
        if (totalLength == 0 || totalLength < lc)
        {
            SendStatusResponse(kSwWrongParameters1, kSwWrongParameters2);
            return;
        }

        mIncomingMessage = System::PacketBufferHandle::New(totalLength, /* aReservedSize = */ 0);
        if (mIncomingMessage.IsNull())
        {
            SendStatusResponse(kSwNotEnoughMemory1, kSwNotEnoughMemory2);
            return;
        }
        mIncomingMessage->SetDataLength(totalLength);
        mIncomingExpectedLength = totalLength;
        mIncomingReceivedLength = 0;
    }
    else if (totalLength != mIncomingExpectedLength)
    {
        ResetIncomingMessage();
        SendStatusResponse(kSwConditionsNotSatisfied1, kSwConditionsNotSatisfied2);
        return;
    }

    if (static_cast<uint32_t>(mIncomingReceivedLength) + lc > mIncomingExpectedLength)
    {
        ResetIncomingMessage();
        SendStatusResponse(kSwNotEnoughMemory1, kSwNotEnoughMemory2);
        return;
    }

    memcpy(mIncomingMessage->Start() + mIncomingReceivedLength, fragment, lc);
    mIncomingReceivedLength = static_cast<uint16_t>(mIncomingReceivedLength + lc);

    if (chained)
    {
        SendStatusResponse(kSwSuccess1, kSwSuccess2);
        return;
    }

    if (mIncomingReceivedLength != mIncomingExpectedLength)
    {
        ResetIncomingMessage();
        SendStatusResponse(kSwWrongParameters1, kSwWrongParameters2);
        return;
    }

    mPendingResponseLe = le;
    DeliverIncomingMessage();
}

void NFCCommissioningManagerImpl::HandleGetResponseCommand(const uint8_t * apdu, size_t apduLength)
{
    if (apduLength != 5 || apdu[0] != 0x00 || apdu[2] != 0x00 || apdu[3] != 0x00)
    {
        SendStatusResponse(kSwWrongParameters1, kSwWrongParameters2);
        return;
    }

    if (!mOutgoingContinuationPending)
    {
        SendStatusResponse(kSwConditionsNotSatisfied1, kSwConditionsNotSatisfied2);
        return;
    }

    SendOutgoingMessageChunk(DecodeShortLe(apdu[4]));
}

void NFCCommissioningManagerImpl::SendStatusResponse(uint8_t sw1, uint8_t sw2)
{
    SendDataResponse(nullptr, 0, sw1, sw2);
}

int NFCCommissioningManagerImpl::SendDataResponse(const uint8_t * data, size_t dataLength, uint8_t sw1, uint8_t sw2)
{
    if (dataLength + 2 > sizeof(mApduBuffer))
    {
        ChipLogError(DeviceLayer, "Response APDU payload of %u bytes doesn't fit in the APDU buffer",
                     static_cast<unsigned int>(dataLength));
        dataLength = 0;
        sw1        = kSwNotEnoughMemory1;
        sw2        = kSwNotEnoughMemory2;
    }

    if (data != nullptr && dataLength > 0)
    {
        memcpy(mApduBuffer, data, dataLength);
    }
    mApduBuffer[dataLength]     = sw1;
    mApduBuffer[dataLength + 1] = sw2;

    int err = nfc_t4t_response_pdu_send(mApduBuffer, dataLength + 2);
    ChipLogDetail(DeviceLayer, "Sent response APDU: SW=%02X%02X, %u byte(s) of data, result=%d", sw1, sw2,
                  static_cast<unsigned int>(dataLength), err);
    if (err)
    {
        ChipLogError(DeviceLayer, "nfc_t4t_response_pdu_send() failed: %d", err);
    }
    return err;
}

void NFCCommissioningManagerImpl::SendOutgoingMessageChunk(size_t le)
{
    if (mOutgoingMessage.IsNull())
    {
        SendStatusResponse(kSwSuccess1, kSwSuccess2);
        return;
    }

    size_t totalLength = mOutgoingMessage->DataLength();
    size_t remaining   = totalLength - mOutgoingOffset;
    size_t chunkLength = std::min(remaining, le);

    const uint8_t * chunk = mOutgoingMessage->Start() + mOutgoingOffset;

    if (remaining - chunkLength == 0)
    {
        if (SendDataResponse(chunk, chunkLength, kSwSuccess1, kSwSuccess2) != 0)
        {
            return;
        }
        mOutgoingMessage             = nullptr;
        mOutgoingOffset              = 0;
        mOutgoingContinuationPending = false;
        mAwaitingApplicationResponse = false;
    }
    else
    {
        uint8_t sw2 = static_cast<uint8_t>((remaining - chunkLength > 0xFF) ? 0 : (remaining - chunkLength));
        if (SendDataResponse(chunk, chunkLength, kSwMoreData1, sw2) != 0)
        {
            return;
        }
        mOutgoingOffset += chunkLength;
        mOutgoingContinuationPending = true;
    }
}

void NFCCommissioningManagerImpl::DeliverIncomingMessage()
{
    if (mNFCBase == nullptr)
    {
        ChipLogError(DeviceLayer, "Cannot deliver incoming NFC message: NFCBase is not initialized");
        ResetIncomingMessage();
        SendStatusResponse(kSwConditionsNotSatisfied1, kSwConditionsNotSatisfied2);
        return;
    }

    struct Context
    {
        Transport::NFCBase * nfcBase;
        Transport::PeerAddress peerAddress;
        System::PacketBufferHandle message;
    };

    auto * context = Platform::New<Context>(Context{ mNFCBase, mPeerAddress, std::move(mIncomingMessage) });
    ResetIncomingMessage();

    if (context == nullptr)
    {
        SendStatusResponse(kSwNotEnoughMemory1, kSwNotEnoughMemory2);
        return;
    }

    CHIP_ERROR err = PlatformMgr().ScheduleWork(
        [](intptr_t arg) {
            auto * ctx = reinterpret_cast<Context *>(arg);
            if (ctx->nfcBase != nullptr)
            {
                ctx->nfcBase->OnNfcTagResponse(ctx->peerAddress, std::move(ctx->message));
            }
            else
            {
                ChipLogError(DeviceLayer, "Dropped incoming NFC message: NFCBase is not initialized");
            }
            ChipLogDetail(DeviceLayer, "DeliverIncomingMessage: OnNfcTagResponse() returned");
            Platform::Delete(ctx);
        },
        reinterpret_cast<intptr_t>(context));

    if (err != CHIP_NO_ERROR)
    {
        ChipLogError(DeviceLayer, "Failed to schedule delivery of incoming NFC message: %" CHIP_ERROR_FORMAT, err.Format());
        Platform::Delete(context);
        SendStatusResponse(kSwNotEnoughMemory1, kSwNotEnoughMemory2);
        return;
    }

    mAwaitingApplicationResponse = true;
}

} // namespace Internal
} // namespace DeviceLayer
} // namespace chip
