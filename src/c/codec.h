#ifndef WISEDB_CODEC_H
#define WISEDB_CODEC_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Explicit little-endian codecs; these do not read or write native structs. */
uint32_t wisedb_load_u32_le(const uint8_t *bytes);
uint64_t wisedb_load_u64_le(const uint8_t *bytes);
void wisedb_store_u32_le(uint8_t *bytes, uint32_t value);
void wisedb_store_u64_le(uint8_t *bytes, uint64_t value);

/* CRC32C (Castagnoli, reflected polynomial), streaming state API. */
uint32_t wisedb_crc32c_extend(uint32_t state, const uint8_t *bytes, size_t size);
uint32_t wisedb_crc32c_finish(uint32_t state);
uint32_t wisedb_crc32c(const uint8_t *bytes, size_t size);

#ifdef __cplusplus
}
#endif

#endif /* WISEDB_CODEC_H */
