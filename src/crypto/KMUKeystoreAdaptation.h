/*
 *    Copyright (c) 2024 Project CHIP Authors
 *    All rights reserved.
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
#pragma once

#include "CHIPCryptoPALPSA.h"

#include <cinttypes>
#include <cracen_psa_kmu.h>

/* KMU Slots for Matter purpose:
 *
 * DAC private key                  176-180 (1 key)
 * NOC private keys (Operational)   155-175 (5 fabrics max)
 * ICD keys                         113-153 (5 fabrics max)
 *
 * DAC private key needs 4 KMU slots (Encrypted)
 * NOC private key needs 4 KMU slots (Encrypted)
 * ICD key needs 3 KMU slots (Not encrypted)
 */
#define KMU_ADAPTATION_USE_ENCRYPTION 0

namespace chip {
namespace Crypto {
namespace KMU {

inline constexpr static uint8_t NOC_Offset         = 155;
inline constexpr static uint8_t ICD_Offset         = 113;
inline constexpr static uint8_t NOC_KeyMax         = 5;
inline constexpr static uint8_t ICD_KeyMax         = 5;
inline constexpr static uint8_t NOC_SingleKeySlots = 2;
inline constexpr static uint8_t ICD_SingleKeySlots = 1;
inline constexpr static uint8_t EncryptionOverhead = 2;

inline CHIP_ERROR GetSlot(psa_key_id_t * keyID, psa_key_attributes_t * attributes)
{
    if (!keyID)
    {
        return CHIP_ERROR_INVALID_ARGUMENT;
    }

    if (static_cast<uint8_t>(*keyID) >= static_cast<uint8_t>(KeyIdBase::Operational) &&
        static_cast<uint8_t>(*keyID) < static_cast<uint8_t>(KeyIdBase::DACPrivKey))
    {
        if (static_cast<uint8_t>(*keyID) > static_cast<uint8_t>(NOC_KeyMax))
        {
            return CHIP_ERROR_PERSISTED_STORAGE_FAILED;
        }

        psa_key_id_t newId = NOC_Offset + ((NOC_SingleKeySlots + EncryptionOverhead) * (*keyID - 1));
        *keyID = static_cast<psa_key_id_t>(PSA_KEY_HANDLE_FROM_CRACEN_KMU_SLOT(CRACEN_KMU_KEY_USAGE_SCHEME_ENCRYPTED, newId));
        if (attributes)
        {
            psa_set_key_lifetime(
                attributes,
                PSA_KEY_LIFETIME_FROM_PERSISTENCE_AND_LOCATION(PSA_KEY_PERSISTENCE_DEFAULT, PSA_KEY_LOCATION_CRACEN_KMU));
        }

        return CHIP_NO_ERROR;
    }
    else if (static_cast<uint8_t>(*keyID) >= static_cast<uint8_t>(KeyIdBase::ICDKeyRangeStart) &&
             static_cast<uint8_t>(*keyID) < static_cast<uint8_t>(KeyIdBase::Maximum))
    {
        if (static_cast<uint8_t>(*keyID) > static_cast<uint8_t>(NOC_KeyMax))
        {
            return CHIP_ERROR_PERSISTED_STORAGE_FAILED;
        }

        psa_key_id_t newId = ICD_Offset + (ICD_SingleKeySlots * (*keyID - 1));
        *keyID = static_cast<psa_key_id_t>(PSA_KEY_HANDLE_FROM_CRACEN_KMU_SLOT(CRACEN_KMU_KEY_USAGE_SCHEME_RAW, newId));

        if (attributes)
        {
            // Cracen KMU supports only PSA_ALG_CCM algorithm, so convert it.
            if (psa_get_key_algorithm(attributes) == PSA_ALG_AEAD_WITH_AT_LEAST_THIS_LENGTH_TAG(PSA_ALG_CCM, 8))
            {
                psa_set_key_algorithm(attributes, PSA_ALG_CCM);
            }

            psa_set_key_lifetime(
                attributes,
                PSA_KEY_LIFETIME_FROM_PERSISTENCE_AND_LOCATION(PSA_KEY_PERSISTENCE_DEFAULT, PSA_KEY_LOCATION_CRACEN_KMU));
        }
        return CHIP_NO_ERROR;
    }

    return CHIP_ERROR_INVALID_ARGUMENT;
}
} // namespace KMU
} // namespace Crypto
} // namespace chip
