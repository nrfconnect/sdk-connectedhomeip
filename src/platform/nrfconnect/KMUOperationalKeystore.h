/*
 *    Copyright (c) 2025 Project CHIP Authors
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

#include "KMUKeyAllocator.h"
#include <crypto/PSAOperationalKeystore.h>

namespace chip {
namespace DeviceLayer {

class KMUOperationalKeystore : public chip::Crypto::PSAOperationalKeystore
{
public:
    CHIP_ERROR CommitOpKeypairForFabric(FabricIndex fabricIndex) override
    {
        VerifyOrReturnError(IsValidFabricIndex(fabricIndex) && mPendingFabricIndex == fabricIndex, CHIP_ERROR_INVALID_FABRIC_INDEX);
        VerifyOrReturnError(mIsPendingKeypairActive, CHIP_ERROR_INCORRECT_STATE);

        psa_status_t status             = PSA_SUCCESS;
        CHIP_ERROR error                = CHIP_NO_ERROR;
        psa_key_attributes_t attributes = PSA_KEY_ATTRIBUTES_INIT;
        psa_key_id_t keyId              = 0;
        uint8_t keyEx[chip::Crypto::kP256_FE_Length];
        size_t keyExSize = sizeof(keyEx);

        PersistentP256Keypair keyPairToCommit(fabricIndex);

        psa_destroy_key(keyPairToCommit.GetKeyId());

        // We cannot use psa_copy_key here because the source and target keys are stored in different locations.
        // Instead we need to export this key and import once again.
        status = psa_export_key(mPendingKeypair->GetKeyId(), keyEx, sizeof(keyEx), &keyExSize);
        VerifyOrExit(status == PSA_SUCCESS, error = CHIP_ERROR_INTERNAL);

        // Switch to the persistent key
        psa_reset_key_attributes(&attributes);
        psa_set_key_type(&attributes, PSA_KEY_TYPE_ECC_KEY_PAIR(PSA_ECC_FAMILY_SECP_R1));
        psa_set_key_bits(&attributes, chip::Crypto::kP256_PrivateKey_Length * 8);
        psa_set_key_algorithm(&attributes, PSA_ALG_ECDSA(PSA_ALG_SHA_256));
        psa_set_key_usage_flags(&attributes, PSA_KEY_USAGE_SIGN_MESSAGE);
        psa_set_key_lifetime(&attributes, PSA_KEY_LIFETIME_PERSISTENT);
        psa_set_key_id(&attributes, keyPairToCommit.GetKeyId());

        status = psa_import_key(&attributes, keyEx, keyExSize, &keyId);
        VerifyOrExit(status == PSA_SUCCESS, error = CHIP_ERROR_INTERNAL);
        VerifyOrExit(keyId == keyPairToCommit.GetKeyId(), error = CHIP_ERROR_INTERNAL);

        // Copied was done, so we can revert the pending keypair
        RevertPendingKeypair();

    exit:
        chip::Crypto::LogPsaError(status);
        psa_reset_key_attributes(&attributes);
        memset(keyEx, 0, sizeof(keyEx));

        return error;
    }
};

} // namespace DeviceLayer
} // namespace chip
