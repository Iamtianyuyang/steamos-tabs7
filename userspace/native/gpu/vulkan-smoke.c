/*
 * Standalone GPU clear/readback test. No window, display or swapchain.
 * Build: cc -O2 -std=c11 -Wall -Wextra vulkan-smoke.c -lvulkan -o vulkan-smoke
 * A timed-out submission exits without destroying in-flight Vulkan objects;
 * process teardown reclaims them. An external timeout can guard driver calls.
 */
#include <vulkan/vulkan.h>

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SIDE 16u
#define BYTE_COUNT (SIDE * SIDE * 4u)
#define FENCE_TIMEOUT_NS UINT64_C(5000000000)

#define CHECK_VK(call) do { \
    VkResult check_result = (call); \
    if (check_result != VK_SUCCESS) { \
        fprintf(stderr, "%s failed: VkResult=%d\n", #call, check_result); \
        goto cleanup; \
    } \
} while (0)

static const char *device_type_name(VkPhysicalDeviceType type)
{
    switch (type) {
    case VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU: return "INTEGRATED_GPU";
    case VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU: return "DISCRETE_GPU";
    case VK_PHYSICAL_DEVICE_TYPE_VIRTUAL_GPU: return "VIRTUAL_GPU";
    case VK_PHYSICAL_DEVICE_TYPE_CPU: return "CPU";
    default: return "OTHER";
    }
}

static const char *driver_name(VkDriverId driver)
{
    switch (driver) {
    case VK_DRIVER_ID_MESA_TURNIP: return "MESA_TURNIP";
    case VK_DRIVER_ID_QUALCOMM_PROPRIETARY: return "QUALCOMM_PROPRIETARY";
    case VK_DRIVER_ID_MESA_LLVMPIPE: return "MESA_LLVMPIPE";
    case VK_DRIVER_ID_GOOGLE_SWIFTSHADER: return "GOOGLE_SWIFTSHADER";
    default: return "OTHER";
    }
}

static VkResult get_driver_properties(VkPhysicalDevice physical,
                                     VkPhysicalDeviceDriverProperties *driver,
                                     int *available)
{
    VkPhysicalDeviceProperties properties;
    VkExtensionProperties *extensions = NULL;
    uint32_t count = 0;
    VkResult result = VK_SUCCESS;
    vkGetPhysicalDeviceProperties(physical, &properties);
    *available = properties.apiVersion >= VK_API_VERSION_1_2;
    if (!*available) {
        result = vkEnumerateDeviceExtensionProperties(physical, NULL, &count, NULL);
        if (result != VK_SUCCESS) return result;
        if (count) {
            extensions = calloc(count, sizeof(*extensions));
            if (!extensions) return VK_ERROR_OUT_OF_HOST_MEMORY;
            result = vkEnumerateDeviceExtensionProperties(physical, NULL, &count, extensions);
            if (result == VK_SUCCESS) {
                for (uint32_t i = 0; i < count; i++) {
                    if (!strcmp(extensions[i].extensionName, VK_KHR_DRIVER_PROPERTIES_EXTENSION_NAME))
                        *available = 1;
                }
            }
            free(extensions);
            if (result != VK_SUCCESS) return result;
        }
    }
    if (*available) {
        VkPhysicalDeviceProperties2 extended = {
            .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
            .pNext = driver,
        };
        vkGetPhysicalDeviceProperties2(physical, &extended);
    }
    return result;
}

static uint32_t find_memory_type(const VkPhysicalDeviceMemoryProperties *memory,
                                uint32_t allowed, VkMemoryPropertyFlags required)
{
    for (uint32_t i = 0; i < memory->memoryTypeCount; i++) {
        if ((allowed & (1u << i)) &&
            (memory->memoryTypes[i].propertyFlags & required) == required)
            return i;
    }
    return UINT32_MAX;
}

int main(void)
{
    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkImage image = VK_NULL_HANDLE;
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory image_memory = VK_NULL_HANDLE;
    VkDeviceMemory buffer_memory = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    VkPhysicalDevice *devices = NULL;
    VkQueueFamilyProperties *families = NULL;
    void *mapped = NULL;
    int exit_code = EXIT_FAILURE;
    uint32_t device_count = 0;
    uint32_t queue_family = UINT32_MAX;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkPhysicalDeviceProperties properties = {0};
    VkPhysicalDeviceDriverProperties driver = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES,
    };
    int driver_available = 0;

    setvbuf(stdout, NULL, _IOLBF, 0);
    VkApplicationInfo application = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "tabs7-vulkan-smoke",
        .apiVersion = VK_API_VERSION_1_1,
    };
    VkInstanceCreateInfo instance_info = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &application,
    };
    CHECK_VK(vkCreateInstance(&instance_info, NULL, &instance));
    CHECK_VK(vkEnumeratePhysicalDevices(instance, &device_count, NULL));
    if (!device_count) {
        fprintf(stderr, "No Vulkan physical devices were found\n");
        goto cleanup;
    }
    devices = calloc(device_count, sizeof(*devices));
    if (!devices) { perror("calloc physical devices"); goto cleanup; }
    CHECK_VK(vkEnumeratePhysicalDevices(instance, &device_count, devices));
    for (uint32_t i = 0; i < device_count; i++) {
        vkGetPhysicalDeviceProperties(devices[i], &properties);
        if (properties.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU) {
            fprintf(stderr, "Rejected CPU device: %s\n", properties.deviceName);
            continue;
        }
        CHECK_VK(get_driver_properties(devices[i], &driver, &driver_available));
        if (driver_available && (driver.driverID == VK_DRIVER_ID_MESA_LLVMPIPE ||
                                 driver.driverID == VK_DRIVER_ID_GOOGLE_SWIFTSHADER)) {
            fprintf(stderr, "Rejected software driver: %s\n", driver.driverName);
            continue;
        }
        uint32_t family_count = 0;
        vkGetPhysicalDeviceQueueFamilyProperties(devices[i], &family_count, NULL);
        if (!family_count) continue;
        families = calloc(family_count, sizeof(*families));
        if (!families) { perror("calloc queue families"); goto cleanup; }
        vkGetPhysicalDeviceQueueFamilyProperties(devices[i], &family_count, families);
        for (uint32_t j = 0; j < family_count; j++) {
            /* ClearColorImage requires a graphics or compute queue. */
            if (families[j].queueCount &&
                (families[j].queueFlags & (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT))) {
                queue_family = j;
                physical = devices[i];
                break;
            }
        }
        free(families);
        families = NULL;
        if (physical != VK_NULL_HANDLE) break;
    }
    if (physical == VK_NULL_HANDLE) {
        fprintf(stderr, "No non-CPU Vulkan device with a graphics/compute queue\n");
        goto cleanup;
    }
    printf("physicalDeviceName=%s\n", properties.deviceName);
    printf("deviceType=%s (%d)\n", device_type_name(properties.deviceType), properties.deviceType);
    printf("vendorID=0x%04x deviceID=0x%04x\n", properties.vendorID, properties.deviceID);
    printf("apiVersion=%u.%u.%u driverVersion=0x%08x\n",
           VK_API_VERSION_MAJOR(properties.apiVersion), VK_API_VERSION_MINOR(properties.apiVersion),
           VK_API_VERSION_PATCH(properties.apiVersion), properties.driverVersion);
    if (driver_available) {
        printf("driverID=%d (%s)\ndriverName=%s\ndriverInfo=%s\n",
               driver.driverID, driver_name(driver.driverID), driver.driverName, driver.driverInfo);
    } else {
        printf("driverID=unavailable (VK_KHR_driver_properties not supported)\n");
    }
    printf("gpuRenderer=%s\nqueueFamily=%u\n", properties.deviceName, queue_family);

    VkFormatProperties format;
    vkGetPhysicalDeviceFormatProperties(physical, VK_FORMAT_R8G8B8A8_UNORM, &format);
    VkFormatFeatureFlags needed = VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
    if ((format.optimalTilingFeatures & needed) != needed) {
        fprintf(stderr, "RGBA8 optimal images do not support both transfer operations\n");
        goto cleanup;
    }
    float priority = 1.0f;
    VkDeviceQueueCreateInfo queue_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = queue_family, .queueCount = 1, .pQueuePriorities = &priority,
    };
    VkDeviceCreateInfo device_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
    };
    CHECK_VK(vkCreateDevice(physical, &device_info, NULL, &device));
    VkQueue queue;
    vkGetDeviceQueue(device, queue_family, 0, &queue);

    VkImageCreateInfo image_info = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_R8G8B8A8_UNORM,
        .extent = {SIDE, SIDE, 1}, .mipLevels = 1, .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };
    CHECK_VK(vkCreateImage(device, &image_info, NULL, &image));
    VkPhysicalDeviceMemoryProperties memory;
    VkMemoryRequirements requirements;
    vkGetPhysicalDeviceMemoryProperties(physical, &memory);
    vkGetImageMemoryRequirements(device, image, &requirements);
    uint32_t image_type = find_memory_type(&memory, requirements.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (image_type == UINT32_MAX) { fprintf(stderr, "No device-local image memory\n"); goto cleanup; }
    VkMemoryAllocateInfo allocation = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = requirements.size, .memoryTypeIndex = image_type,
    };
    CHECK_VK(vkAllocateMemory(device, &allocation, NULL, &image_memory));
    CHECK_VK(vkBindImageMemory(device, image, image_memory, 0));
    VkBufferCreateInfo buffer_info = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = BYTE_COUNT, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    CHECK_VK(vkCreateBuffer(device, &buffer_info, NULL, &buffer));
    vkGetBufferMemoryRequirements(device, buffer, &requirements);
    uint32_t buffer_type = find_memory_type(&memory, requirements.memoryTypeBits,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (buffer_type == UINT32_MAX)
        buffer_type = find_memory_type(&memory, requirements.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT);
    if (buffer_type == UINT32_MAX) { fprintf(stderr, "No host-visible readback memory\n"); goto cleanup; }
    allocation.allocationSize = requirements.size;
    allocation.memoryTypeIndex = buffer_type;
    CHECK_VK(vkAllocateMemory(device, &allocation, NULL, &buffer_memory));
    CHECK_VK(vkBindBufferMemory(device, buffer, buffer_memory, 0));

    VkCommandPoolCreateInfo pool_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex = queue_family, .flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT,
    };
    CHECK_VK(vkCreateCommandPool(device, &pool_info, NULL, &pool));
    VkCommandBuffer command;
    VkCommandBufferAllocateInfo command_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1,
    };
    CHECK_VK(vkAllocateCommandBuffers(device, &command_info, &command));
    VkCommandBufferBeginInfo begin_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    CHECK_VK(vkBeginCommandBuffer(command, &begin_info));
    VkImageSubresourceRange subresources = {
        .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .levelCount = 1, .layerCount = 1,
    };
    VkImageMemoryBarrier image_barrier = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .srcAccessMask = 0, .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED, .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = image, .subresourceRange = subresources,
    };
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         0, 0, NULL, 0, NULL, 1, &image_barrier);
    VkClearColorValue clear = {.float32 = {0.25f, 0.5f, 0.75f, 1.0f}};
    vkCmdClearColorImage(command, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &clear, 1, &subresources);
    image_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    image_barrier.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    image_barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    image_barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         0, 0, NULL, 0, NULL, 1, &image_barrier);
    VkBufferImageCopy copy = {
        .imageSubresource = {.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .layerCount = 1},
        .imageExtent = {SIDE, SIDE, 1},
    };
    vkCmdCopyImageToBuffer(command, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1, &copy);
    VkBufferMemoryBarrier host_barrier = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = buffer, .offset = 0, .size = VK_WHOLE_SIZE,
    };
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                         0, 0, NULL, 1, &host_barrier, 0, NULL);
    CHECK_VK(vkEndCommandBuffer(command));
    VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    CHECK_VK(vkCreateFence(device, &fence_info, NULL, &fence));
    VkSubmitInfo submission = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1, .pCommandBuffers = &command,
    };
    CHECK_VK(vkQueueSubmit(queue, 1, &submission, fence));
    VkResult wait_result = vkWaitForFences(device, 1, &fence, VK_TRUE, FENCE_TIMEOUT_NS);
    if (wait_result != VK_SUCCESS) {
        fprintf(stderr, "GPU submission did not complete: VkResult=%d%s\n", wait_result,
                wait_result == VK_TIMEOUT ? " (5-second fence timeout)" : "");
        if (wait_result != VK_ERROR_DEVICE_LOST) {
            /* Pending objects cannot be legally destroyed, and WaitIdle is unbounded. */
            fprintf(stderr, "Exiting without Vulkan object destruction; process teardown reclaims pending resources\n");
            fflush(NULL);
            _Exit(EXIT_FAILURE);
        }
        goto cleanup;
    }
    CHECK_VK(vkMapMemory(device, buffer_memory, 0, VK_WHOLE_SIZE, 0, &mapped));
    if (!(memory.memoryTypes[buffer_type].propertyFlags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
        VkMappedMemoryRange invalidate = {
            .sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
            .memory = buffer_memory, .offset = 0, .size = VK_WHOLE_SIZE,
        };
        CHECK_VK(vkInvalidateMappedMemoryRanges(device, 1, &invalidate));
    }
    const uint8_t expected[4] = {64, 128, 191, 255};
    const uint8_t *pixels = mapped;
    for (uint32_t i = 0; i < SIDE * SIDE; i++) {
        for (uint32_t channel = 0; channel < 4; channel++) {
            int difference = (int)pixels[i * 4 + channel] - expected[channel];
            if (difference < -1 || difference > 1) {
                fprintf(stderr, "Readback mismatch: pixel=%u channel=%u got=%u expected=%u tolerance=1\n",
                        i, channel, pixels[i * 4 + channel], expected[channel]);
                goto cleanup;
            }
        }
    }
    printf("readbackRGBA=%u,%u,%u,%u\npixelsChecked=%u tolerance=1\nGPU_CLEAR_READBACK_PASS\n",
           pixels[0], pixels[1], pixels[2], pixels[3], SIDE * SIDE);
    exit_code = EXIT_SUCCESS;

cleanup:
    if (mapped) vkUnmapMemory(device, buffer_memory);
    if (fence != VK_NULL_HANDLE) vkDestroyFence(device, fence, NULL);
    if (pool != VK_NULL_HANDLE) vkDestroyCommandPool(device, pool, NULL);
    if (buffer != VK_NULL_HANDLE) vkDestroyBuffer(device, buffer, NULL);
    if (buffer_memory != VK_NULL_HANDLE) vkFreeMemory(device, buffer_memory, NULL);
    if (image != VK_NULL_HANDLE) vkDestroyImage(device, image, NULL);
    if (image_memory != VK_NULL_HANDLE) vkFreeMemory(device, image_memory, NULL);
    if (device != VK_NULL_HANDLE) vkDestroyDevice(device, NULL);
    if (instance != VK_NULL_HANDLE) vkDestroyInstance(instance, NULL);
    free(families);
    free(devices);
    return exit_code;
}
