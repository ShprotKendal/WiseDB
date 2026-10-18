#include "codec.h"

uint32_t wisedb_load_u32_le(const uint8_t *bytes) {
    return (uint32_t)bytes[0]
        | ((uint32_t)bytes[1] << 8)
        | ((uint32_t)bytes[2] << 16)
        | ((uint32_t)bytes[3] << 24);
}

uint64_t wisedb_load_u64_le(const uint8_t *bytes) {
    uint64_t value = 0;
    for (unsigned i = 0; i < 8; ++i) value |= (uint64_t)bytes[i] << (i * 8);
    return value;
}

void wisedb_store_u32_le(uint8_t *bytes, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) bytes[i] = (uint8_t)(value >> (i * 8));
}

void wisedb_store_u64_le(uint8_t *bytes, uint64_t value) {
    for (unsigned i = 0; i < 8; ++i) bytes[i] = (uint8_t)(value >> (i * 8));
}

uint32_t wisedb_crc32c_extend(uint32_t state, const uint8_t *bytes, size_t size) {
    for (size_t i = 0; i < size; ++i) {
        state ^= bytes[i];
        for (unsigned bit = 0; bit < 8; ++bit) {
            uint32_t mask = (uint32_t)-(int32_t)(state & 1u);
            state = (state >> 1) ^ (0x82f63b78u & mask);
        }
    }
    return state;
}

uint32_t wisedb_crc32c_finish(uint32_t state) {
    return ~state;
}

uint32_t wisedb_crc32c(const uint8_t *bytes, size_t size) {
    return wisedb_crc32c_finish(wisedb_crc32c_extend(UINT32_MAX, bytes, size));
}
