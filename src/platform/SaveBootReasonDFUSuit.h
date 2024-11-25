/*
 * Copyright (c) 2021 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef NRF_SAVE_BOOT_REASON_H__
#define NRF_SAVE_BOOT_REASON_H__

inline int SoftwareBootReasonSUIT __attribute__((section(".noinit")));

inline int getSoftwareRebootReasonSUIT()
{
    return SoftwareBootReasonSUIT;
}

inline void setSoftwareRebootReasonSUIT(int reason)
{
    SoftwareBootReasonSUIT = reason;
}

#endif