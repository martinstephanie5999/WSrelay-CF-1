import test from "node:test";
import assert from "node:assert/strict";

import {
  PROTOCOL_VERSION,
  MAGIC_0,
  MAGIC_1,
  HEADER_SIZE,
  FLAG_FINAL,
  CHUNK_SIZE,
  WINDOW_SIZE,
  PERSIST_EVERY_CHUNKS,
  MIN_CHUNK_SIZE,
  MAX_CHUNK_SIZE,
  MAX_WINDOW_SIZE,
  CONTROL_MAX_BYTES,
  EARLY_DATA_MAX_BYTES,
  CLOSE_REPLACED,
  CLOSE_PROTOCOL_VERSION,
  CLOSE_ROOM_FULL,
  CLOSE_ROOM_EXPIRED,
  CLOSE_WRONG_PASSWORD,
  ROOM_TTL_MS,
} from "../../src/protocol.js";

test("wire version and frame header are frozen", () => {
  assert.equal(PROTOCOL_VERSION, 1);
  assert.equal(MAGIC_0, 0x52);
  assert.equal(MAGIC_1, 0x4c);
  assert.equal(HEADER_SIZE, 16);
  assert.equal(FLAG_FINAL, 1);
});

test("window defaults never exceed the 2 MiB burst cap", () => {
  assert.equal(CHUNK_SIZE, 256 * 1024);
  assert.equal(WINDOW_SIZE, 8);
  assert.ok(PERSIST_EVERY_CHUNKS <= WINDOW_SIZE);
  assert.equal(CHUNK_SIZE * WINDOW_SIZE, 2 * 1024 * 1024);
  assert.ok(MIN_CHUNK_SIZE <= CHUNK_SIZE);
  assert.ok(MAX_CHUNK_SIZE >= CHUNK_SIZE);
  assert.ok(MAX_WINDOW_SIZE >= WINDOW_SIZE);
});

test("control and early-data frames stay small", () => {
  assert.ok(CONTROL_MAX_BYTES > 0 && CONTROL_MAX_BYTES <= 64 * 1024);
  assert.ok(EARLY_DATA_MAX_BYTES > 0 && EARLY_DATA_MAX_BYTES <= 16 * 1024);
});

test("close codes and room TTL are stable", () => {
  const codes = [
    CLOSE_REPLACED,
    CLOSE_PROTOCOL_VERSION,
    CLOSE_ROOM_FULL,
    CLOSE_ROOM_EXPIRED,
    CLOSE_WRONG_PASSWORD,
  ];
  assert.deepEqual(codes, [4001, 4002, 4003, 4004, 4005]);
  assert.equal(ROOM_TTL_MS, 24 * 60 * 60 * 1000);
});
