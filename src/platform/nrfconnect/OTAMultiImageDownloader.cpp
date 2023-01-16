/*
 *
 *    Copyright (c) 2022 Project CHIP Authors
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

#include "OTAMultiImageDownloader.h"
#include <dfu/dfu_target.h>
#include <platform/CHIPDeviceLayer.h>
#include <pm_config.h>

namespace chip {
namespace DeviceLayer {

size_t OTAMultiImageDownloaders::sCurrentId;

std::array<OTAMultiImageDownloader, OTAMultiImageDownloaders::CurrentImageID::LIMIT> OTAMultiImageDownloaders::sImageHandlers{
    { OTAMultiImageDownloader{ 0, PM_APP_1_CORE_APP_DOWNLOAD_OFFSET, PM_APP_1_CORE_APP_DOWNLOAD_SIZE, PM_APP_1_CORE_APP_ADDRESS,
                               PM_APP_1_CORE_APP_SIZE },
      OTAMultiImageDownloader{ 1, PM_APP_1_CORE_NET_DOWNLOAD_OFFSET, PM_APP_1_CORE_NET_DOWNLOAD_SIZE, PM_APP_1_CORE_NET_ADDRESS,
                               PM_APP_1_CORE_NET_SIZE },
      OTAMultiImageDownloader{ 2, PM_APP_2_CORE_APP_DOWNLOAD_OFFSET, PM_APP_2_CORE_APP_DOWNLOAD_SIZE, PM_APP_2_CORE_APP_ADDRESS,
                               PM_APP_2_CORE_APP_SIZE },
      OTAMultiImageDownloader{ 3, PM_APP_2_CORE_NET_DOWNLOAD_OFFSET, PM_APP_2_CORE_NET_DOWNLOAD_SIZE, PM_APP_2_CORE_NET_ADDRESS,
                               PM_APP_2_CORE_NET_SIZE } }
};

static const device * sExtFlash = DEVICE_DT_GET(DT_CHOSEN(nordic_pm_ext_flash));

int OTAMultiImageDownloader::Init(const device * flash, ssize_t size)
{
    if (!device_is_ready(flash))
    {
        ChipLogError(SoftwareUpdate, "Flash device is not ready!");
        return -EFAULT;
    }
    return Init(flash, mOffset, size);
}

int OTAMultiImageDownloader::Init(const device * flash, size_t offset, size_t size)
{
    mBuffer = (uint8_t *) Platform::MemoryAlloc(kBufferSize);
    return stream_flash_init(&mStream, flash, mBuffer, kBufferSize, offset, size, nullptr);
}

int OTAMultiImageDownloader::Apply(const device * flash)
{
    // Reinit stream with different flash partition
    int err = Init(flash, mTargetOffset, mTargetImgSize);
    if (err != 0)
    {
        ChipLogError(SoftwareUpdate, "Cannot reinitialize flash stream");
        return -EBUSY;
    }

    Platform::ScopedMemoryBuffer<uint8_t> buffer;
    if (!buffer.Alloc(kBufferSize))
    {
        return -ENOMEM;
    }

    for (size_t offset = 0; offset < mImgSize; offset += kBufferSize)
    {
        err = flash_read(flash, mOffset + offset, buffer.Get(), kBufferSize);
        if (err != 0)
        {
            ChipLogError(SoftwareUpdate, "Failed to read data chunk from flash");
            return -EIO;
        }
        err = Write(buffer.Get(), kBufferSize);
        if (err != 0)
        {
            ChipLogError(SoftwareUpdate, "Failed to write data chunk to flash");
            return -EIO;
        }
    }
    return Finalize();
}

int OTAMultiImageDownloader::Write(const uint8_t * chunk, size_t chunkSize)
{
    return stream_flash_buffered_write(&mStream, chunk, chunkSize, false);
}

int OTAMultiImageDownloader::Finalize()
{
    int err = stream_flash_buffered_write(&mStream, nullptr, 0, true);
    Platform::MemoryFree(mBuffer);
    return err;
}

// Implementation of stateless OTAMultiImageDownloaders class

int OTAMultiImageDownloaders::Open(int id, size_t size)
{
    int dfuTargetId = id < 2 ? id : id - 2; // dfu_target only accepts fixed 0 and 1 slots in the secondary partition
    if (DfuTargetActionNeeded(id))
    {

        int err = dfu_target_init(DFU_TARGET_IMAGE_TYPE_MCUBOOT, dfuTargetId, size, nullptr);
        if (err)
        {
            ChipLogError(SoftwareUpdate, "Cannot initialize DFU target: %d", err);
            return err;
        }
    }

    OTAMultiImageDownloaders::SetCurrentImageId(id);
    return OTAMultiImageDownloaders::GetCurrentImage()->Init(sExtFlash, size);
}

int OTAMultiImageDownloaders::Write(const uint8_t * chunk, size_t chunk_size)
{
    if (DfuTargetActionNeeded(OTAMultiImageDownloaders::CurrentImageId()))
    {
        int err = dfu_target_write(chunk, chunk_size);
        if (err < 0)
        {
            ChipLogError(SoftwareUpdate, "Cannot write DFU target: %d", err);
            return err;
        }
    }

    return OTAMultiImageDownloaders::GetCurrentImage()->Write(chunk, chunk_size);
}

int OTAMultiImageDownloaders::Close(bool success)
{
    if (DfuTargetActionNeeded(OTAMultiImageDownloaders::CurrentImageId()))
    {
        int err = success ? dfu_target_done(success) : dfu_target_reset();
        if (err)
        {
            ChipLogError(SoftwareUpdate, "Cannot close DFU target: %d", err);
            return err;
        }
    }

    return OTAMultiImageDownloaders::GetCurrentImage()->Finalize();
}

int OTAMultiImageDownloaders::Apply()
{
    int err{ -ENXIO };
    for (auto image : sImageHandlers)
    {
        err = image.Apply(sExtFlash);
        if (err)
        {
            return err;
        }
    }
    return err;
}

int OTAMultiImageDownloaders::DfuTargetActionNeeded(int id)
{
    if (CONFIG_APPLICATION_IDX == RunningVariant::THREAD && CONFIG_APPLICATION_OTHER_IDX == RunningVariant::WIFI)
    {
        return (id == CurrentImageID::APP_1_CORE_APP || id == CurrentImageID::APP_1_CORE_NET);
    }
    else if (CONFIG_APPLICATION_IDX == RunningVariant::WIFI && CONFIG_APPLICATION_OTHER_IDX == RunningVariant::THREAD)
    {
        return (id == CurrentImageID::APP_2_CORE_APP || id == CurrentImageID::APP_2_CORE_NET);
    }
    return false;
}

} // namespace DeviceLayer
} // namespace chip
