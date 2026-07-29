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

/**
 *    @file
 *          Provides an implementation of the NFCCommissioningManager singleton object for
 *          nRF Connect SDK platforms.
 *
 *          nRF Connect SDK devices are Matter accessories: this implementation therefore only
 *          supports the Commissionee ("NFC Listener"/Tag) role of the NFC Transport Layer (NTL).
 */

#pragma once

#include <platform/internal/NFCCommissioningManager.h>

#include <transport/raw/NfcApplicationDelegate.h>

#include <lib/core/Global.h>
#include <system/SystemPacketBuffer.h>

#include <nfc_t4t_lib.h>

#include <cstdint>

namespace chip {
namespace DeviceLayer {

struct ChipDeviceEvent;

namespace Internal {

/**
 * Concrete implementation of the NFCCommissioningManagerImpl singleton object for nRF Connect SDK platform.
 */
class NFCCommissioningManagerImpl final : public NFCCommissioningManager, private Nfc::NfcApplicationDelegate
{
    // Allow the NFCCommissioningManager interface class to delegate method calls to
    // the implementation methods provided by this class.
    friend NFCCommissioningManager;

public:
    NFCCommissioningManagerImpl() {}

    // ===== Members that implement virtual methods on NfcApplicationDelegate.

    void SetNFCBase(Transport::NFCBase * nfcBase) override;
    bool CanSendToPeer(const Transport::PeerAddress & address) override;
    CHIP_ERROR SendToNfcTag(const Transport::PeerAddress & address, System::PacketBufferHandle && msgBuf) override;
    bool HasOnboardingPayload() const
    {
        return mHasOnboardingPayload && mRawIsoDepStarted && !mBlockMatterAidSelection && !mNfcEmulationPausedForFailSafe;
    }
    CHIP_ERROR ConfigureOnboardingPayload();

    // Maximum size of the NDEF message (URI record) served by the NDEF Tag Application's NDEF
    // file, and total size of that file (message plus its 2-byte NLEN length header).
    static constexpr size_t kNdefMessageCapacity = 128;
    static constexpr size_t kNdefFileBufferSize  = kNdefMessageCapacity + 2;

private:
    // ===== Members that implement the NFCCommissioningManager internal interface.

    CHIP_ERROR _Init();
    void _Shutdown();
    Nfc::NFCReaderTransport * _GetNFCReaderTransport() const { return nullptr; }
    void _SetNFCReaderTransport(Nfc::NFCReaderTransport *) {}

    CHIP_ERROR SetOnboardingPayload(const char * payload, size_t payloadLength);
    CHIP_ERROR ClearOnboardingPayload();

    /**
     * Platform event handler used to detect when commissioning has completed, so that
     * the NFC tag emulation can be stopped since it is no longer needed.
     */
    static void OnPlatformEvent(const ChipDeviceEvent * event, intptr_t arg);

    /** Stops NFC tag emulation once the device has been successfully commissioned. */
    void HandleCommissioningComplete();

    /** Starts NFC tag emulation once the server is up and the device has no fabrics yet. */
    void StartNfcCommissioning();

    static void ScheduledStartNfcCommissioning(intptr_t arg);

    /** Logs that NFC Matter transport can accept a new session after fail-safe expiry. */
    void HandleFailSafeTimerExpired();

    /** Blocks new Matter AID selection once an NFC PASE session is established. */
    void HandleSecureSessionEstablished(const ChipDeviceEvent * event);

    // Grant header-local singleton accessors access to sInstance.
    friend NFCCommissioningManager & NFCCommissioningMgr();
    friend NFCCommissioningManagerImpl & NFCCommissioningMgrImpl();

    static Global<NFCCommissioningManagerImpl> sInstance;

    static constexpr size_t kMaxApduSize         = 261;
    static constexpr uint8_t kNtlProtocolVersion = 0x01;

    // Applications selectable within our single raw ISO-DEP session (see file header).
    enum class SelectedApplication
    {
        kNone,
        kMatter,
        kNdef,
    };

    // Elementary files selectable under the NDEF Tag Application, once selected.
    enum class SelectedFile
    {
        kNone,
        kCapabilityContainer,
        kNdefFile,
    };

    /** Handle Type 4 Tag (ISO-DEP) event */
    static void OnT4TEvent(void * context, nfc_t4t_event_t event, const uint8_t * data, size_t dataLength, uint32_t flags);

    /**
     * Starts the emulation of a raw ISO-DEP (Type 4 Tag) NFC tag,
     * enabling the device to receive APDU commands from an external NFC reader.
     */
    void StartRawIsoDepTagEmulation();

    /** Stops raw ISO-DEP tag emulation without removing the platform event handler. */
    void StopRawIsoDepTagEmulation();

    /** Stops tag emulation while fail-safe rollback is in progress. */
    void PauseNfcTagEmulationForFailSafe();

    /** Returns true when there is no in-flight ISO-DEP/Matter session state. */
    bool IsSessionIdle() const;

    /**
     * Handles the event indicating that an NFC polling device (reader) field is present.
     * Typically used to prepare for communication when a reader initiates contact.
     */
    void HandleFieldOn();

    /**
     * Handles the event indicating that the NFC field has been turned off, meaning
     * the polling device (reader) is no longer present or has moved out of range.
     * Used for cleanup or session reset.
     */
    void HandleFieldOff();

    /**
     * Handles an incoming data indication event from the NFC stack.
     * Receives a fragment of APDU data sent from the reader and processes it as part of the command stream.
     *
     * @param data Pointer to the received data buffer.
     * @param dataLength Length of the data buffer in bytes.
     * @param flags Optional NFC stack flags associated with this indication.
     */
    void HandleDataIndication(const uint8_t * data, size_t dataLength, uint32_t flags);

    /**
     * Handles notification from the NFC stack that a previously requested data transmission
     * (to the NFC reader) has been completed.
     */
    void HandleDataTransmitted();

    /**
     * Processes a complete or fragmented APDU command received from the NFC reader,
     * potentially reconstructing multi-part commands and dispatching to appropriate handlers.
     *
     * @param apdu Pointer to the APDU command data.
     * @param apduLength Length of the command data.
     * @param fragmentCount Number of fragments in the current APDU command.
     */
    void ProcessCommandApdu(const uint8_t * apdu, size_t apduLength, size_t fragmentCount);

    /**
     * Handles an APDU SELECT command, determining whether the request is for the Matter AID
     * or the NDEF Tag Application, and updating session state accordingly.
     *
     * @param apdu Pointer to the SELECT command data.
     * @param apduLength Length of the command data.
     */
    void HandleSelectCommand(const uint8_t * apdu, size_t apduLength);

    /**
     * Handles a SELECT command by application identifier (AID), such as selecting
     * the Matter application or NDEF Tag application.
     *
     * @param aid Pointer to the application identifier data.
     * @param aidLength Length of the AID in bytes.
     */
    void HandleSelectByNameCommand(const uint8_t * aid, size_t aidLength);

    /**
     * Handles a SELECT command targeting a specific elementary file by its 2-byte file identifier
     * within the active NFC application.
     *
     * @param fileId Pointer to the file identifier data (2 bytes).
     */
    void HandleSelectByFileIdCommand(const uint8_t * fileId);

    /**
     * Handles the event when the Matter Application Identifier (AID) has been successfully selected
     * in an NFC session; prepares for APDU communication for commissioning.
     */
    void HandleMatterAidSelected();

    /**
     * Handles a READ BINARY APDU command, returning the requested part of the current file
     * (such as the NDEF message or Capability Container) to the NFC reader.
     *
     * @param apdu Pointer to the READ BINARY command data.
     * @param apduLength Length of the command data.
     */
    void HandleReadBinaryCommand(const uint8_t * apdu, size_t apduLength);

    /**
     * Handles a TRANSPORT APDU command, used to transmit Matter protocol messages
     * over NFC, reassembling or transmitting them as needed.
     *
     * @param apdu Pointer to the TRANSPORT command data.
     * @param apduLength Length of the command data.
     */
    void HandleTransportCommand(const uint8_t * apdu, size_t apduLength);

    /**
     * Handles a GET RESPONSE APDU command, allowing the NFC reader to retrieve
     * additional response data from previous commands, if available.
     *
     * @param apdu Pointer to the GET RESPONSE command data.
     * @param apduLength Length of the command data.
     */
    void HandleGetResponseCommand(const uint8_t * apdu, size_t apduLength);

    /**
     * Sends a status response APDU to the NFC reader, containing only status words (SW1, SW2)
     * and no additional data.
     *
     * @param sw1 The first status word byte.
     * @param sw2 The second status word byte.
     */
    void SendStatusResponse(uint8_t sw1, uint8_t sw2);

    /**
     * Sends an APDU response containing a data payload and status words (SW1, SW2) to the NFC reader.
     *
     * @param data Pointer to the response data buffer.
     * @param dataLength Length of the response data.
     * @param sw1 The first status word byte.
     * @param sw2 The second status word byte.
     * @return 0 if successful, otherwise a negative error code.
     */
    int SendDataResponse(const uint8_t * data, size_t dataLength, uint8_t sw1, uint8_t sw2);

    /**
     * Sends a chunk (up to 'le' bytes) of the currently buffered outgoing Matter message
     * to the NFC reader, used to support large message transfers with APDU fragmenting.
     *
     * @param le The maximum number of bytes to send in this chunk.
     */
    void SendOutgoingMessageChunk(size_t le);

    /**
     * Resets all session state and internal buffers for the NFC commissioning manager,
     * preparing for a new reader session or to recover from errors.
     *
     * @param notifyAborted  If true, notify the Matter stack when an active Matter session is aborted.
     */
    void ResetSession(bool notifyAborted = false);

    /**
     * Resets session state. Caller must hold the session mutex.
     *
     * @param notifyAborted  If true, notify the Matter stack when an active Matter session is aborted.
     */
    void ResetSessionState(bool notifyAborted = false);

    /**
     * Schedules a call to NFCBase::OnNfcTagError() on the Matter thread.
     */
    void ScheduleNfcTagError(Transport::NFCBase * nfcBase, const Transport::PeerAddress & peerAddress);

    /**
     * Delivers a fully reassembled incoming Matter message, received via NFC,
     * to the Matter application layer for processing.
     */
    void DeliverIncomingMessage();

    /**
     * Clears the in-progress incoming Matter message reassembly state.
     * Caller must hold the session mutex.
     */
    void ResetIncomingMessage();

    /**
     * Rejects an unsupported SELECT command and clears application/file selection.
     * Caller must hold the session mutex.
     */
    void RejectUnsupportedSelect(uint8_t p1, uint8_t p2);

    // ===== Members for internal use

    Transport::NFCBase * mNFCBase            = nullptr;
    bool mRawIsoDepStarted                   = false;
    SelectedApplication mSelectedApplication = SelectedApplication::kNone;
    SelectedFile mSelectedFile               = SelectedFile::kNone;
    size_t mPendingResponseLe                = 0;
    bool mOutgoingContinuationPending        = false;
    Transport::PeerAddress mPeerAddress      = Transport::PeerAddress::NFC();
    uint8_t mApduBuffer[kMaxApduSize];
    size_t mApduLength        = 0;
    size_t mApduFragmentCount = 0;
    System::PacketBufferHandle mIncomingMessage;
    uint16_t mIncomingExpectedLength = 0;
    uint16_t mIncomingReceivedLength = 0;
    System::PacketBufferHandle mOutgoingMessage;
    size_t mOutgoingOffset = 0;

    // NDEF Tag Application's NDEF file contents: a 2-byte big-endian NLEN header (mNdefFileBuffer[0..1])
    // followed by the NDEF message itself, kept contiguous to simplify offset-based READ BINARY
    // handling. NLEN == 0 (the power-up default) is a valid empty-file state per the NFC Forum Type
    // 4 Tag specification.
    uint8_t mNdefFileBuffer[kNdefFileBufferSize] = { 0x00, 0x00 };
    size_t mNdefFileLength                       = 2;
    bool mHasOnboardingPayload                   = false;

    // Set once an incoming message has been fully reassembled and handed off to the Matter stack.
    bool mAwaitingApplicationResponse = false;

    // While set, NDEF reads and new Matter AID selections are rejected, and the
    // onboarding payload is cleared, so a second NFC tap cannot restart commissioning
    // during fail-safe rollback.
    bool mBlockMatterAidSelection = false;

    // Set when tag emulation is stopped during fail-safe rollback. While set, all NFC
    // callbacks are ignored to avoid corrupting the NFC platform ring buffer on re-tap.
    bool mNfcEmulationPausedForFailSafe = false;
};

/**
 * Returns a reference to the public interface of the NFCCommissioningManager singleton object.
 *
 * Internal components should use this to access features of the NFCCommissioningManager object
 * that are common to all platforms.
 */
inline NFCCommissioningManager & NFCCommissioningMgr()
{
    return NFCCommissioningManagerImpl::sInstance.get();
}

/**
 * Returns the platform-specific implementation of the NFCCommissioningManager singleton object.
 *
 * Internal components can use this to gain access to features of the NFCCommissioningManager
 * that are specific to nRF Connect SDK platforms.
 */
inline NFCCommissioningManagerImpl & NFCCommissioningMgrImpl()
{
    return NFCCommissioningManagerImpl::sInstance.get();
}

} // namespace Internal
} // namespace DeviceLayer
} // namespace chip
