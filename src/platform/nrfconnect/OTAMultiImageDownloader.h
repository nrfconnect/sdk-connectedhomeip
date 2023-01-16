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

#include <zephyr/storage/stream_flash.h>

#include <array>

namespace chip {
namespace DeviceLayer {

/* A helper class to facilitate the multi variant application OTA DFU
   (Thread/WiFi switchable)  with the stream_flash backend */
class OTAMultiImageDownloader
{
public:
    OTAMultiImageDownloader(int imageId, size_t offset, size_t size, size_t targetOffset, size_t targetSize) :
        mImgId(imageId), mOffset(offset), mImgSize(size), mTargetOffset(targetOffset), mTargetImgSize(targetSize)
    {}

    int Init(const device * flash, ssize_t size);
    int Write(const uint8_t * chunk, size_t chunkSize);
    int Finalize();
    int Apply(const device * flash);
    int GetImageId() { return mImgId; }

private:
    int Init(const device * flash, size_t offset, size_t size);

    int mImgId;
    const size_t mOffset;
    const size_t mImgSize;
    const size_t mTargetOffset;
    const size_t mTargetImgSize;
    uint8_t * mBuffer;
    stream_flash_ctx mStream;

    static constexpr size_t kBufferSize = CONFIG_CHIP_OTA_REQUESTOR_BUFFER_SIZE;
};

/* Wrapper for a collection of OTAMultiImageDownloader static objects */
class OTAMultiImageDownloaders
{
public:
    static OTAMultiImageDownloader * GetCurrentImage()
    {
        if (sCurrentId > sImageHandlers.size())
        {
            return nullptr;
        }
        return &sImageHandlers[sCurrentId];
    }

    static int Open(int id, size_t size);
    static int Write(const uint8_t * chunk, size_t chunk_size);
    static int Close(bool success);
    static int Apply();
    static int DfuTargetActionNeeded(int id);
    static void SetCurrentImageId(size_t id) { sCurrentId = id; }
    static size_t CurrentImageId() { return sCurrentId; }

private:
    enum CurrentImageID : int
    {
        NONE = -1,
        APP_1_CORE_APP,
        APP_1_CORE_NET,
        APP_2_CORE_APP,
        APP_2_CORE_NET,
        LIMIT
    };

    enum RunningVariant : int
    {
        THREAD = 1,
        WIFI
    };

    static size_t sCurrentId;
    static std::array<OTAMultiImageDownloader, CurrentImageID::LIMIT> sImageHandlers;
};

} // namespace DeviceLayer
} // namespace chip
