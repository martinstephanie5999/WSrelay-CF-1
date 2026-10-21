// Protocol constants shared by the Worker, Durable Object and the browser client.
// PROTOCOL.md v1.5 (plus 2026-10-21 hardening) is authoritative; this file
// only encodes its frozen defaults.

export const PROTOCOL_VERSION = 1;

export const MAGIC_0 = 0x52; // "R"
export const MAGIC_1 = 0x4c; // "L"
export const HEADER_SIZE = 16;
export const FLAG_FINAL = 1;
export const FLAG_ENCRYPTED = 2;

export const CHUNK_SIZE = 256 * 1024; // 256 KiB; 8-in-flight = 2 MiB burst cap
export const WINDOW_SIZE = 8;
export const PERSIST_EVERY_CHUNKS = 8;
export const MIN_CHUNK_SIZE = 256 * 1024;
export const MAX_CHUNK_SIZE = 4 * 1024 * 1024;
export const MAX_WINDOW_SIZE = 32;

export const CONTROL_MAX_BYTES = 64 * 1024;
export const EARLY_DATA_MAX_BYTES = 4 * 1024;
export const MAX_FILE_SIZE = Number.MAX_SAFE_INTEGER;

export const CLOSE_REPLACED = 4001;
export const CLOSE_PROTOCOL_VERSION = 4002;
export const CLOSE_ROOM_FULL = 4003;
export const CLOSE_ROOM_EXPIRED = 4004;
export const CLOSE_WRONG_PASSWORD = 4005;
export const CLOSE_INTERNAL = 1011;

export const ROOM_TTL_MS = 24 * 60 * 60 * 1000;
