#ifndef SCAPI_STAGE4_EVENT_TRANSPORT_H
#define SCAPI_STAGE4_EVENT_TRANSPORT_H

/*
 * Stage-4 bounded event transport.
 *
 * This header is game-independent.  It owns no storage, performs no
 * allocation, performs no I/O, and never waits for an external model.
 * The caller supplies the complete ring buffer and an explicit capacity;
 * there is deliberately no production default capacity, retention policy,
 * or eviction policy.
 *
 * Record layout (all fields are little-endian in the target process, but the
 * transport treats them as native uint32_t values):
 *
 *   offset  field
 *   0       epoch
 *   4       sequence (assigned by the producer; never deduplicated by text)
 *   8       tick (observed game tick)
 *   12      type (opaque caller value)
 *   16      x (signed)
 *   20      y (signed)
 *   24      color
 *   28      text_bytes (0..218)
 *   32      text[218]   EXACT payload capacity; bytes beyond text_bytes are
 *                       zero-filled and are not part of the record content
 *   250     payload_padding[2]
 *
 * The 218-byte capacity comes from the target display-line copy length
 * 0xDA (218) in evidence/stage4/start-20261108/static/text-window.asm:
 * 0x469447..0x469454 clamps the copied length to 0xDA, and 0x46966B /
 * 0x4696DA copy 0xDA bytes.  The transport stores the exact 218-byte
 * payload plus an explicit byte count; it does not append a NUL and it
 * never truncates an oversized record.
 *
 * Publishing / consistency protocol
 * ---------------------------------
 * ONE producer (the game UI thread) and ONE external reader/acknowledger
 * are supported.  The producer is the only writer of write_seq, and the
 * reader is the only writer of ack_seq.  Append and acknowledge may run on
 * different threads/processes.
 *
 * Producer append:
 *   1. validate the whole input (epoch, text size, free space) before
 *      touching a slot;
 *   2. fill the slot's record, write record_hash, then release-store the
 *      slot's commit_seq (the publication marker);
 *   3. under the write_meta seqlock, advance write_seq and published.
 * A reader that acquire-loads write_seq therefore sees a fully published
 * slot.  Slots are never overwritten while unacknowledged: if
 * write_seq - ack_seq == capacity, append returns SCAPI_EVENT_E_FULL
 * immediately and sets the sticky overflow/lost state.  There is no
 * eviction, no partial record, and no silent truncation.
 *
 * Reader:
 *   1. acquire-load write_seq and ack_seq;
 *   2. for a sequence in [ack_seq, write_seq), acquire-load the slot's
 *      commit_seq and verify the record sequence/epoch/text_bytes/hash;
 *   3. persist the bytes externally;
 *   4. only after persistence, acknowledge a contiguous prefix with
 *      scapi_event_ack().  The native side validates that every
 *      acknowledged slot is published and consistent, but it cannot and
 *      does not verify external persistence: ack is a caller contract.
 *
 * Sticky flags:
 *   SCAPI_EVENT_FLAG_OVERFLOW  set on a full-ring or oversized-text reject;
 *                              never cleared for the epoch
 *   SCAPI_EVENT_FLAG_INVALID   set on a detected slot/ack inconsistency;
 *                              never cleared for the epoch
 *   SCAPI_EVENT_FLAG_EXHAUSTED set when the 32-bit sequence space is
 *                              exhausted (sequence 0xFFFFFFFF is reserved)
 *
 * Rejections do NOT consume a sequence.  Equal text at different sequences
 * is recorded as distinct events; there is no content deduplication.
 * Status is a best-effort seqlock snapshot; status.consistent is 0 when a
 * producer/acknowledger update overlapped the copy and the caller should
 * retry.  Slot data itself is protected by publish/acquire, not by status.
 */

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define SCAPI_EVENT_MAGIC       0x34564553u /* "SEV4" in little-endian */
#define SCAPI_EVENT_VERSION     1u
#define SCAPI_EVENT_TEXT_BYTES  218u
#define SCAPI_EVENT_RECORD_BYTES 252u
#define SCAPI_EVENT_SLOT_BYTES   260u
#define SCAPI_EVENT_HEADER_BYTES 120u
#define SCAPI_EVENT_ALIGN        4u
#define SCAPI_EVENT_MAX_SEQUENCE 0xFFFFFFFEu

/* Error values are stable transport diagnostics, not playable state. */
enum {
    SCAPI_EVENT_OK = 0,
    SCAPI_EVENT_E_ARGUMENT = 1,
    SCAPI_EVENT_E_LAYOUT = 2,
    SCAPI_EVENT_E_STATE = 3,
    SCAPI_EVENT_E_EPOCH = 4,
    SCAPI_EVENT_E_FULL = 5,
    SCAPI_EVENT_E_OVERSIZE = 6,
    SCAPI_EVENT_E_EXHAUSTED = 7,
    SCAPI_EVENT_E_RANGE = 8,
    SCAPI_EVENT_E_ACK_RANGE = 9,
    SCAPI_EVENT_E_ACK_INVALID = 10,
    SCAPI_EVENT_E_CORRUPT = 11
};

enum {
    SCAPI_EVENT_FLAG_OVERFLOW  = 1u << 0,
    SCAPI_EVENT_FLAG_INVALID   = 1u << 1,
    SCAPI_EVENT_FLAG_EXHAUSTED = 1u << 2
};

#define SCAPI_EVENT_FLAGS_KNOWN \
    (SCAPI_EVENT_FLAG_OVERFLOW | SCAPI_EVENT_FLAG_INVALID | \
     SCAPI_EVENT_FLAG_EXHAUSTED)

#if defined(__TINYC__)
#include <windows.h>
/* The target is 32-bit Windows. Interlocked operations provide aligned
   atomic accesses and a full barrier even when TinyCC has no __atomic API. */
static uint32_t scapi_ev_atomic_or(volatile uint32_t *p,uint32_t value)
{
    LONG before,after;
    do {
        before=InterlockedCompareExchange((volatile LONG*)p,0,0);
        after=before|(LONG)value;
    } while(InterlockedCompareExchange((volatile LONG*)p,after,before)!=before);
    return (uint32_t)before;
}
static void scapi_ev_fence(void)
{
    LONG barrier=0;
    InterlockedExchange(&barrier,1);
}
#define SCAPI_EV_LOAD_ACQUIRE(p) ((uint32_t)InterlockedCompareExchange((volatile LONG*)(p),0,0))
#define SCAPI_EV_LOAD_RELAXED(p) SCAPI_EV_LOAD_ACQUIRE(p)
#define SCAPI_EV_STORE_RELEASE(p,v) ((void)InterlockedExchange((volatile LONG*)(p),(LONG)(v)))
#define SCAPI_EV_FENCE_ACQUIRE() scapi_ev_fence()
#define SCAPI_EV_FENCE_RELEASE() scapi_ev_fence()
#define SCAPI_EV_ATOMIC_OR(p,v) scapi_ev_atomic_or((p),(v))
#define SCAPI_EV_ATOMIC_ADD(p,v) ((uint32_t)InterlockedExchangeAdd((volatile LONG*)(p),(LONG)(v)))
#elif defined(_MSC_VER)
#include <intrin.h>
#define SCAPI_EV_LOAD_ACQUIRE(p)  (_ReadWriteBarrier(), *(p))
#define SCAPI_EV_LOAD_RELAXED(p)  (*(p))
#define SCAPI_EV_STORE_RELEASE(p, v) \
    do { _ReadWriteBarrier(); *(p) = (v); _ReadWriteBarrier(); } while (0)
#define SCAPI_EV_FENCE_ACQUIRE()  _ReadWriteBarrier()
#define SCAPI_EV_FENCE_RELEASE()  _ReadWriteBarrier()
#define SCAPI_EV_ATOMIC_OR(p, v) \
    ((uint32_t)_InterlockedOr((volatile long *)(p), (long)(v)))
#define SCAPI_EV_ATOMIC_ADD(p, v) \
    ((uint32_t)_InterlockedExchangeAdd((volatile long *)(p), (long)(v)))
#elif defined(__GNUC__) || defined(__clang__)
#define SCAPI_EV_LOAD_ACQUIRE(p)  __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define SCAPI_EV_LOAD_RELAXED(p)  __atomic_load_n((p), __ATOMIC_RELAXED)
#define SCAPI_EV_STORE_RELEASE(p, v) \
    __atomic_store_n((p), (v), __ATOMIC_RELEASE)
#define SCAPI_EV_FENCE_ACQUIRE()  __atomic_thread_fence(__ATOMIC_ACQUIRE)
#define SCAPI_EV_FENCE_RELEASE()  __atomic_thread_fence(__ATOMIC_RELEASE)
#define SCAPI_EV_ATOMIC_OR(p, v) \
    __atomic_fetch_or((p), (v), __ATOMIC_ACQ_REL)
#define SCAPI_EV_ATOMIC_ADD(p, v) \
    __atomic_fetch_add((p), (v), __ATOMIC_ACQ_REL)
#else
#error "SCAPI event transport requires GCC/Clang atomics or MSVC intrinsics"
#endif

#if defined(_MSC_VER)
#define SCAPI_EVENT_INLINE static __inline
#else
#define SCAPI_EVENT_INLINE static inline
#endif

#define SCAPI_EVENT_ASSERT_CAT_(a, b) a##b
#define SCAPI_EVENT_ASSERT_CAT(a, b) SCAPI_EVENT_ASSERT_CAT_(a, b)
#define SCAPI_EVENT_STATIC_ASSERT(cond, msg) \
    typedef char SCAPI_EVENT_ASSERT_CAT(scapi_event_assert_, __LINE__)[(cond) ? 1 : -1]

#pragma pack(push, 4)

typedef struct ScapiEventRecord {
    uint32_t epoch;
    uint32_t sequence;
    uint32_t tick;
    uint32_t type;
    int32_t  x;
    int32_t  y;
    uint32_t color;
    uint32_t text_bytes;
    uint8_t  text[SCAPI_EVENT_TEXT_BYTES];
    uint8_t  payload_padding[2];
} ScapiEventRecord;

typedef struct ScapiEventSlot {
    /* Written last by the producer; 0 means no published record. */
    volatile uint32_t commit_seq;
    uint32_t record_hash;
    ScapiEventRecord record;
} ScapiEventSlot;

typedef struct ScapiEventHeader {
    uint32_t magic;
    uint32_t version;
    uint32_t header_bytes;
    uint32_t slot_bytes;
    uint32_t record_bytes;
    uint32_t capacity;
    uint32_t epoch;
    uint32_t text_bytes;
    uint32_t record_text_offset;
    uint32_t slot_commit_offset;
    uint32_t slot_hash_offset;
    uint32_t reserved0;

    /* Producer-owned sequence/publication state, guarded by write_meta. */
    volatile uint32_t write_seq;
    volatile uint32_t ack_seq;
    volatile uint32_t write_meta;
    volatile uint32_t ack_meta;
    volatile uint32_t flags;
    volatile uint32_t lost;
    volatile uint32_t rejected_full;
    volatile uint32_t rejected_oversize;
    volatile uint32_t rejected_epoch;
    volatile uint32_t rejected_argument;
    volatile uint32_t published;
    volatile uint32_t acked;
    volatile uint32_t invalid_events;
    volatile uint32_t last_error;
    uint32_t reserved[4];
} ScapiEventHeader;

#pragma pack(pop)

/* In-process input descriptor; it is not part of the shared-memory layout
   and therefore keeps the compiler's natural pointer alignment. */
typedef struct ScapiEventInput {
    uint32_t epoch;
    uint32_t tick;
    uint32_t type;
    int32_t  x;
    int32_t  y;
    uint32_t color;
    const uint8_t *text;
    uint32_t text_bytes;
} ScapiEventInput;

typedef struct ScapiEventStatus {
    uint32_t epoch;
    uint32_t capacity;
    uint32_t text_bytes;
    uint32_t write_seq;
    uint32_t ack_seq;
    uint32_t pending;
    uint32_t published;
    uint32_t acked;
    uint32_t flags;
    uint32_t lost;
    uint32_t rejected_full;
    uint32_t rejected_oversize;
    uint32_t rejected_epoch;
    uint32_t rejected_argument;
    uint32_t invalid_events;
    uint32_t last_error;
    uint32_t consistent;
} ScapiEventStatus;

SCAPI_EVENT_STATIC_ASSERT(SCAPI_EVENT_TEXT_BYTES == 218u,
                          "display-line payload must stay 218 bytes");
SCAPI_EVENT_STATIC_ASSERT(sizeof(ScapiEventRecord) == SCAPI_EVENT_RECORD_BYTES,
                          "record layout changed");
SCAPI_EVENT_STATIC_ASSERT(offsetof(ScapiEventRecord, text) == 32u,
                          "text payload offset changed");
SCAPI_EVENT_STATIC_ASSERT(sizeof(ScapiEventSlot) == SCAPI_EVENT_SLOT_BYTES,
                          "slot layout changed");
SCAPI_EVENT_STATIC_ASSERT(sizeof(ScapiEventHeader) == SCAPI_EVENT_HEADER_BYTES,
                          "header layout changed");
SCAPI_EVENT_STATIC_ASSERT(offsetof(ScapiEventHeader, write_seq) == 48u,
                          "write_seq offset changed");
SCAPI_EVENT_STATIC_ASSERT(offsetof(ScapiEventHeader, flags) == 64u,
                          "flags offset changed");

/* Required bytes for a caller-provided ring, or 0 on capacity 0/overflow. */
SCAPI_EVENT_INLINE size_t scapi_event_required_bytes(uint32_t capacity)
{
    size_t header = sizeof(ScapiEventHeader);
    size_t slot = sizeof(ScapiEventSlot);
    if (capacity == 0u) return 0u;
    if ((size_t)capacity > (SIZE_MAX - header) / slot) return 0u;
    return header + (size_t)capacity * slot;
}

SCAPI_EVENT_INLINE int scapi_event_validate(const void *storage)
{
    const ScapiEventHeader *h;
    uint32_t pending;

    if (!storage) return SCAPI_EVENT_E_ARGUMENT;
    if (((uintptr_t)storage & (SCAPI_EVENT_ALIGN - 1u)) != 0u)
        return SCAPI_EVENT_E_ARGUMENT;
    h = (const ScapiEventHeader *)storage;
    if (h->magic != SCAPI_EVENT_MAGIC ||
        h->version != SCAPI_EVENT_VERSION) return SCAPI_EVENT_E_LAYOUT;
    if (h->header_bytes != (uint32_t)sizeof(ScapiEventHeader) ||
        h->slot_bytes != (uint32_t)sizeof(ScapiEventSlot) ||
        h->record_bytes != (uint32_t)sizeof(ScapiEventRecord))
        return SCAPI_EVENT_E_LAYOUT;
    if (h->capacity == 0u || scapi_event_required_bytes(h->capacity) == 0u)
        return SCAPI_EVENT_E_LAYOUT;
    if (h->text_bytes != SCAPI_EVENT_TEXT_BYTES ||
        h->record_text_offset != (uint32_t)offsetof(ScapiEventRecord, text) ||
        h->slot_commit_offset != (uint32_t)offsetof(ScapiEventSlot, commit_seq) ||
        h->slot_hash_offset != (uint32_t)offsetof(ScapiEventSlot, record_hash))
        return SCAPI_EVENT_E_LAYOUT;
    if ((SCAPI_EV_LOAD_ACQUIRE(&h->flags) & ~SCAPI_EVENT_FLAGS_KNOWN) != 0u)
        return SCAPI_EVENT_E_LAYOUT;
    if (h->write_seq == 0u || h->ack_seq == 0u) return SCAPI_EVENT_E_LAYOUT;
    if (h->ack_seq > h->write_seq) return SCAPI_EVENT_E_LAYOUT;
    pending = h->write_seq - h->ack_seq;
    if (pending > h->capacity) return SCAPI_EVENT_E_LAYOUT;
    return SCAPI_EVENT_OK;
}

/*
 * Initialize caller-provided storage.  bytes must cover the exact required
 * layout for capacity; larger mappings are allowed.  Re-initialization is
 * refused while any event is unacknowledged (E_STATE), so an init cannot
 * silently discard a published record.  Zero the storage first to recover
 * from a corrupted leftover header.  A drained transport may be
 * re-initialized, which restarts at sequence 1; the caller must open a new
 * journal run for that new sequence space.
 */
SCAPI_EVENT_INLINE int scapi_event_init(void *storage, size_t bytes,
                                        uint32_t capacity, uint32_t epoch)
{
    ScapiEventHeader *h;
    size_t required;

    if (!storage || capacity == 0u) return SCAPI_EVENT_E_ARGUMENT;
    if (((uintptr_t)storage & (SCAPI_EVENT_ALIGN - 1u)) != 0u)
        return SCAPI_EVENT_E_ARGUMENT;
    required = scapi_event_required_bytes(capacity);
    if (required == 0u || bytes < required) return SCAPI_EVENT_E_ARGUMENT;

    if (*(const uint32_t *)storage == SCAPI_EVENT_MAGIC) {
        int validation = scapi_event_validate(storage);
        if (validation != SCAPI_EVENT_OK) return SCAPI_EVENT_E_LAYOUT;
        if (validation == SCAPI_EVENT_OK) {
            const ScapiEventHeader *old =
                (const ScapiEventHeader *)storage;
            if (old->write_seq != old->ack_seq)
                return SCAPI_EVENT_E_STATE;
        }
    }

    memset(storage, 0, required);
    h = (ScapiEventHeader *)storage;
    h->magic = SCAPI_EVENT_MAGIC;
    h->version = SCAPI_EVENT_VERSION;
    h->header_bytes = (uint32_t)sizeof(ScapiEventHeader);
    h->slot_bytes = (uint32_t)sizeof(ScapiEventSlot);
    h->record_bytes = (uint32_t)sizeof(ScapiEventRecord);
    h->capacity = capacity;
    h->epoch = epoch;
    h->text_bytes = SCAPI_EVENT_TEXT_BYTES;
    h->record_text_offset = (uint32_t)offsetof(ScapiEventRecord, text);
    h->slot_commit_offset = (uint32_t)offsetof(ScapiEventSlot, commit_seq);
    h->slot_hash_offset = (uint32_t)offsetof(ScapiEventSlot, record_hash);
    h->reserved0 = 0u;
    h->write_seq = 1u;
    h->ack_seq = 1u;
    h->write_meta = 0u;
    h->ack_meta = 0u;
    h->flags = 0u;
    h->lost = 0u;
    h->rejected_full = 0u;
    h->rejected_oversize = 0u;
    h->rejected_epoch = 0u;
    h->rejected_argument = 0u;
    h->published = 0u;
    h->acked = 0u;
    h->invalid_events = 0u;
    h->last_error = 0u;
    SCAPI_EV_FENCE_RELEASE();
    return SCAPI_EVENT_OK;
}

SCAPI_EVENT_INLINE uint32_t scapi_event_hash_bytes(uint32_t hash,
                                                    const void *data,
                                                    size_t bytes)
{
    const uint8_t *p = (const uint8_t *)data;
    while (bytes--) {
        hash ^= (uint32_t)*p++;
        hash *= 16777619u; /* FNV-1a 32-bit */
    }
    return hash;
}

/* Hash covers only defined record fields and text_bytes payload bytes. */
SCAPI_EVENT_INLINE uint32_t scapi_event_record_hash(const ScapiEventRecord *r)
{
    uint32_t hash = 2166136261u;
    if (!r || r->text_bytes > SCAPI_EVENT_TEXT_BYTES) return 0u;
    hash = scapi_event_hash_bytes(hash, &r->epoch, sizeof(r->epoch));
    hash = scapi_event_hash_bytes(hash, &r->sequence, sizeof(r->sequence));
    hash = scapi_event_hash_bytes(hash, &r->tick, sizeof(r->tick));
    hash = scapi_event_hash_bytes(hash, &r->type, sizeof(r->type));
    hash = scapi_event_hash_bytes(hash, &r->x, sizeof(r->x));
    hash = scapi_event_hash_bytes(hash, &r->y, sizeof(r->y));
    hash = scapi_event_hash_bytes(hash, &r->color, sizeof(r->color));
    hash = scapi_event_hash_bytes(hash, &r->text_bytes, sizeof(r->text_bytes));
    hash = scapi_event_hash_bytes(hash, r->text, (size_t)r->text_bytes);
    return hash;
}

SCAPI_EVENT_INLINE ScapiEventSlot *scapi_event_slot_mut(ScapiEventHeader *h,
                                                        uint32_t sequence)
{
    size_t index = (size_t)((sequence - 1u) % h->capacity);
    return (ScapiEventSlot *)((uint8_t *)h + sizeof(ScapiEventHeader) +
                              index * sizeof(ScapiEventSlot));
}

SCAPI_EVENT_INLINE const ScapiEventSlot *
scapi_event_slot_const(const ScapiEventHeader *h, uint32_t sequence)
{
    size_t index = (size_t)((sequence - 1u) % h->capacity);
    return (const ScapiEventSlot *)((const uint8_t *)h +
                                    sizeof(ScapiEventHeader) +
                                    index * sizeof(ScapiEventSlot));
}

SCAPI_EVENT_INLINE void scapi_event_meta_begin(volatile uint32_t *meta)
{
    uint32_t value = SCAPI_EV_LOAD_RELAXED(meta);
    SCAPI_EV_STORE_RELEASE(meta, value + 1u);
    SCAPI_EV_FENCE_RELEASE();
}

SCAPI_EVENT_INLINE void scapi_event_meta_end(volatile uint32_t *meta)
{
    uint32_t value = SCAPI_EV_LOAD_RELAXED(meta);
    SCAPI_EV_STORE_RELEASE(meta, value + 1u);
}

SCAPI_EVENT_INLINE void scapi_event_mark_invalid(ScapiEventHeader *h,
                                                 uint32_t error)
{
    SCAPI_EV_ATOMIC_OR(&h->flags, SCAPI_EVENT_FLAG_INVALID);
    SCAPI_EV_ATOMIC_ADD(&h->invalid_events, 1u);
    SCAPI_EV_STORE_RELEASE(&h->last_error, error);
}

/*
 * Append one complete event.  Returns OK and the assigned sequence, or a
 * negative-free error code.  On E_FULL/E_OVERSIZE the sticky overflow flag
 * and lost counter are updated and no sequence/slot is consumed.  The
 * producer must be the same UI thread for every call; there is no internal
 * lock and no wait.
 */
SCAPI_EVENT_INLINE int scapi_event_append(void *storage,
                                          const ScapiEventInput *input,
                                          uint32_t *out_sequence)
{
    ScapiEventHeader *h;
    ScapiEventSlot *slot;
    ScapiEventRecord *record;
    uint32_t sequence;
    uint32_t hash;
    uint32_t ack;
    int validation;

    if (out_sequence) *out_sequence = 0u;
    if (!input) return SCAPI_EVENT_E_ARGUMENT;
    validation = scapi_event_validate(storage);
    if (validation != SCAPI_EVENT_OK) return validation;
    h = (ScapiEventHeader *)storage;

    /* Reject oversize before touching a slot.  The whole event is rejected. */
    if (input->text_bytes > SCAPI_EVENT_TEXT_BYTES) {
        scapi_event_meta_begin(&h->write_meta);
        SCAPI_EV_ATOMIC_OR(&h->flags, SCAPI_EVENT_FLAG_OVERFLOW);
        h->lost += 1u;
        h->rejected_oversize += 1u;
        SCAPI_EV_STORE_RELEASE(&h->last_error, SCAPI_EVENT_E_OVERSIZE);
        scapi_event_meta_end(&h->write_meta);
        return SCAPI_EVENT_E_OVERSIZE;
    }
    if (input->text_bytes != 0u && input->text == NULL) {
        scapi_event_meta_begin(&h->write_meta);
        h->rejected_argument += 1u;
        SCAPI_EV_STORE_RELEASE(&h->last_error, SCAPI_EVENT_E_ARGUMENT);
        scapi_event_meta_end(&h->write_meta);
        return SCAPI_EVENT_E_ARGUMENT;
    }
    if (input->epoch != h->epoch) {
        scapi_event_meta_begin(&h->write_meta);
        h->rejected_epoch += 1u;
        SCAPI_EV_STORE_RELEASE(&h->last_error, SCAPI_EVENT_E_EPOCH);
        scapi_event_meta_end(&h->write_meta);
        return SCAPI_EVENT_E_EPOCH;
    }

    sequence = h->write_seq; /* producer-owned; no other writer */
    if (sequence > SCAPI_EVENT_MAX_SEQUENCE) {
        SCAPI_EV_ATOMIC_OR(&h->flags, SCAPI_EVENT_FLAG_EXHAUSTED);
        scapi_event_mark_invalid(h, SCAPI_EVENT_E_EXHAUSTED);
        return SCAPI_EVENT_E_EXHAUSTED;
    }
    ack = SCAPI_EV_LOAD_ACQUIRE(&h->ack_seq);
    if ((uint32_t)(sequence - ack) >= h->capacity) {
        scapi_event_meta_begin(&h->write_meta);
        SCAPI_EV_ATOMIC_OR(&h->flags, SCAPI_EVENT_FLAG_OVERFLOW);
        h->lost += 1u;
        h->rejected_full += 1u;
        SCAPI_EV_STORE_RELEASE(&h->last_error, SCAPI_EVENT_E_FULL);
        scapi_event_meta_end(&h->write_meta);
        return SCAPI_EVENT_E_FULL;
    }

    slot = scapi_event_slot_mut(h, sequence);
    memset(&slot->record, 0, sizeof(slot->record));
    record = &slot->record;
    record->epoch = input->epoch;
    record->sequence = sequence;
    record->tick = input->tick;
    record->type = input->type;
    record->x = input->x;
    record->y = input->y;
    record->color = input->color;
    record->text_bytes = input->text_bytes;
    if (input->text_bytes != 0u)
        memcpy(record->text, input->text, (size_t)input->text_bytes);
    hash = scapi_event_record_hash(record);
    slot->record_hash = hash;
    SCAPI_EV_STORE_RELEASE(&slot->commit_seq, sequence);

    scapi_event_meta_begin(&h->write_meta);
    h->write_seq = sequence + 1u;
    h->published += 1u;
    scapi_event_meta_end(&h->write_meta);

    if (out_sequence) *out_sequence = sequence;
    return SCAPI_EVENT_OK;
}

/*
 * Read one published, not-yet-acknowledged record.  The caller copies the
 * record out; no storage pointer is retained and no I/O occurs here.
 */
SCAPI_EVENT_INLINE int scapi_event_read(void *storage, uint32_t sequence,
                                        ScapiEventRecord *out_record)
{
    ScapiEventHeader *h;
    const ScapiEventSlot *slot;
    uint32_t ack;
    uint32_t write;
    uint32_t commit;
    uint32_t hash;
    int validation;

    if (!out_record) return SCAPI_EVENT_E_ARGUMENT;
    validation = scapi_event_validate(storage);
    if (validation != SCAPI_EVENT_OK) return validation;
    h = (ScapiEventHeader *)storage;
    ack = SCAPI_EV_LOAD_ACQUIRE(&h->ack_seq);
    write = SCAPI_EV_LOAD_ACQUIRE(&h->write_seq);
    if (sequence < ack || sequence >= write) return SCAPI_EVENT_E_RANGE;

    slot = scapi_event_slot_const(h, sequence);
    commit = SCAPI_EV_LOAD_ACQUIRE(&slot->commit_seq);
    if (commit != sequence || slot->record.sequence != sequence ||
        slot->record.epoch != h->epoch ||
        slot->record.text_bytes > SCAPI_EVENT_TEXT_BYTES) {
        scapi_event_mark_invalid(h, SCAPI_EVENT_E_CORRUPT);
        return SCAPI_EVENT_E_CORRUPT;
    }
    hash = scapi_event_record_hash(&slot->record);
    if (hash != slot->record_hash) {
        scapi_event_mark_invalid(h, SCAPI_EVENT_E_CORRUPT);
        return SCAPI_EVENT_E_CORRUPT;
    }
    memmove(out_record, &slot->record, sizeof(*out_record));
    return SCAPI_EVENT_OK;
}

/*
 * Acknowledge the contiguous prefix [current ack_seq, next_sequence).  The
 * caller MUST have durably persisted every record in that range first; this
 * function only verifies that each slot is published and consistent.  It
 * cannot verify persistence on the caller's behalf.  One acknowledger only;
 * there is no internal lock and no wait.
 */
SCAPI_EVENT_INLINE int scapi_event_ack(void *storage, uint32_t next_sequence)
{
    ScapiEventHeader *h;
    uint32_t ack;
    uint32_t write;
    uint32_t sequence;
    int validation;

    validation = scapi_event_validate(storage);
    if (validation != SCAPI_EVENT_OK) return validation;
    h = (ScapiEventHeader *)storage;
    ack = SCAPI_EV_LOAD_ACQUIRE(&h->ack_seq);
    write = SCAPI_EV_LOAD_ACQUIRE(&h->write_seq);
    if (next_sequence < ack || next_sequence > write)
        return SCAPI_EVENT_E_ACK_RANGE;

    for (sequence = ack; sequence < next_sequence; ++sequence) {
        const ScapiEventSlot *slot = scapi_event_slot_const(h, sequence);
        uint32_t commit = SCAPI_EV_LOAD_ACQUIRE(&slot->commit_seq);
        uint32_t hash;
        if (commit != sequence || slot->record.sequence != sequence ||
            slot->record.epoch != h->epoch ||
            slot->record.text_bytes > SCAPI_EVENT_TEXT_BYTES) {
            scapi_event_mark_invalid(h, SCAPI_EVENT_E_ACK_INVALID);
            return SCAPI_EVENT_E_ACK_INVALID;
        }
        hash = scapi_event_record_hash(&slot->record);
        if (hash != slot->record_hash) {
            scapi_event_mark_invalid(h, SCAPI_EVENT_E_ACK_INVALID);
            return SCAPI_EVENT_E_ACK_INVALID;
        }
    }

    if (next_sequence != ack) {
        scapi_event_meta_begin(&h->ack_meta);
        h->ack_seq = next_sequence;
        h->acked += (next_sequence - ack);
        scapi_event_meta_end(&h->ack_meta);
    }
    return SCAPI_EVENT_OK;
}

typedef struct ScapiEventProducerView {
    uint32_t write_seq;
    uint32_t published;
    uint32_t lost;
    uint32_t rejected_full;
    uint32_t rejected_oversize;
    uint32_t rejected_epoch;
    uint32_t rejected_argument;
} ScapiEventProducerView;

typedef struct ScapiEventAckView {
    uint32_t ack_seq;
    uint32_t acked;
} ScapiEventAckView;

SCAPI_EVENT_INLINE int
scapi_event_read_write_view(const ScapiEventHeader *h,
                            ScapiEventProducerView *view)
{
    uint32_t attempt;
    for (attempt = 0u; attempt < 16u; ++attempt) {
        uint32_t before = SCAPI_EV_LOAD_ACQUIRE(&h->write_meta);
        uint32_t after;
        if ((before & 1u) != 0u) continue;
        view->write_seq = SCAPI_EV_LOAD_ACQUIRE(&h->write_seq);
        view->published = SCAPI_EV_LOAD_ACQUIRE(&h->published);
        view->lost = SCAPI_EV_LOAD_ACQUIRE(&h->lost);
        view->rejected_full = SCAPI_EV_LOAD_ACQUIRE(&h->rejected_full);
        view->rejected_oversize =
            SCAPI_EV_LOAD_ACQUIRE(&h->rejected_oversize);
        view->rejected_epoch = SCAPI_EV_LOAD_ACQUIRE(&h->rejected_epoch);
        view->rejected_argument =
            SCAPI_EV_LOAD_ACQUIRE(&h->rejected_argument);
        SCAPI_EV_FENCE_ACQUIRE();
        after = SCAPI_EV_LOAD_ACQUIRE(&h->write_meta);
        if (before == after && (after & 1u) == 0u) return 1;
    }
    return 0;
}

SCAPI_EVENT_INLINE int
scapi_event_read_ack_view(const ScapiEventHeader *h, ScapiEventAckView *view)
{
    uint32_t attempt;
    for (attempt = 0u; attempt < 16u; ++attempt) {
        uint32_t before = SCAPI_EV_LOAD_ACQUIRE(&h->ack_meta);
        uint32_t after;
        if ((before & 1u) != 0u) continue;
        view->ack_seq = SCAPI_EV_LOAD_ACQUIRE(&h->ack_seq);
        view->acked = SCAPI_EV_LOAD_ACQUIRE(&h->acked);
        SCAPI_EV_FENCE_ACQUIRE();
        after = SCAPI_EV_LOAD_ACQUIRE(&h->ack_meta);
        if (before == after && (after & 1u) == 0u) return 1;
    }
    return 0;
}

/*
 * Best-effort lock-free status snapshot.  consistent == 0 means a producer
 * or acknowledger update overlapped the copy; the numeric fields may be a
 * mixture and the caller should retry.  Slot publication correctness does
 * not depend on this status call.
 */
SCAPI_EVENT_INLINE int scapi_event_status(const void *storage,
                                          ScapiEventStatus *out_status)
{
    const ScapiEventHeader *h;
    ScapiEventProducerView producer;
    ScapiEventAckView acknowledgment;
    int producer_ok;
    int ack_ok;
    int validation;

    if (!out_status) return SCAPI_EVENT_E_ARGUMENT;
    validation = scapi_event_validate(storage);
    if (validation != SCAPI_EVENT_OK) return validation;
    h = (const ScapiEventHeader *)storage;
    memset(&producer, 0, sizeof(producer));
    memset(&acknowledgment, 0, sizeof(acknowledgment));
    producer_ok = scapi_event_read_write_view(h, &producer);
    ack_ok = scapi_event_read_ack_view(h, &acknowledgment);

    out_status->epoch = h->epoch;
    out_status->capacity = h->capacity;
    out_status->text_bytes = h->text_bytes;
    out_status->write_seq = producer.write_seq;
    out_status->ack_seq = acknowledgment.ack_seq;
    out_status->pending = (producer.write_seq >= acknowledgment.ack_seq)
                              ? producer.write_seq - acknowledgment.ack_seq
                              : 0u;
    out_status->published = producer.published;
    out_status->acked = acknowledgment.acked;
    out_status->flags = SCAPI_EV_LOAD_ACQUIRE(&h->flags);
    out_status->lost = producer.lost;
    out_status->rejected_full = producer.rejected_full;
    out_status->rejected_oversize = producer.rejected_oversize;
    out_status->rejected_epoch = producer.rejected_epoch;
    out_status->rejected_argument = producer.rejected_argument;
    out_status->invalid_events =
        SCAPI_EV_LOAD_ACQUIRE(&h->invalid_events);
    out_status->last_error = SCAPI_EV_LOAD_ACQUIRE(&h->last_error);
    out_status->consistent = (producer_ok && ack_ok) ? 1u : 0u;
    return SCAPI_EVENT_OK;
}

#endif /* SCAPI_STAGE4_EVENT_TRANSPORT_H */
