# Skill: Slice Hot Path

## When to use this skill
When modifying `routers/slices.py`, `services/slice_service.py`, or anything
that touches the binary CT slice streaming route. This is the most
performance-sensitive route in the sidecar — every MPR scroll event fires it.

---

## Route contract

```
GET /api/v1/datasets/{dataset_id}/slice?axis=axial&index=120&lod=native
```

**Response:**
- Content-Type: `application/octet-stream`
- Body: raw `int16` little-endian pixel data, row-major (width × height × 2 bytes)
- Header: `X-Slice-Meta: {"width": 512, "height": 512, "min": -1024, "max": 3071}`

The frontend reads `X-Slice-Meta` first to know dimensions and HU range before
rendering the raw buffer. Never change this header shape without updating
`src/gateway/LocalDataGateway.ts` in AxiomaUX.

---

## Current implementation: `services/slice_service.py`

```python
async def get_slice_bytes(dataset_id, axis, index, lod) -> tuple[bytes, width, height, min, max]:
    # 1. Look up dataset path from SQLite (datasets table, path column)
    # 2. Offload to run_in_executor → _extract_slice(dataset_path, axis, index)
    # 3. _extract_slice: loads DICOM series with pydicom, assembles (Z,Y,X) int16 volume,
    #    applies RescaleSlope/RescaleIntercept, extracts the requested plane
    # 4. Returns (bytes, width, height, min_val, max_val)
```

The `dataset_path` stored in SQLite is the absolute path to a DICOM CT folder
(written there by `session_service.mount_dataset()`).

---

## Performance rules

1. **Always use `run_in_executor`** — pydicom file I/O is blocking. Never `await`
   directly on disk reads in an async route.

2. **Volume cache** — loading the full DICOM series on every scroll is too slow.
   Add a module-level LRU cache keyed on `dataset_path`:

   ```python
   from functools import lru_cache

   @lru_cache(maxsize=4)
   def _load_ct_volume_cached(dataset_path: str) -> np.ndarray:
       ...
   ```

   `maxsize=4` keeps up to 4 patient volumes in memory. The string key ensures
   the same path reuses the cached array. Call `_load_ct_volume_cached.cache_clear()`
   if a dataset is remounted.

3. **Sort slices by z-position** — DICOM series files are not guaranteed to be
   ordered. Always sort by `float(ds.ImagePositionPatient[2])` before stacking.

4. **Axis mapping** (DICOM LPS → array index):
   - `axial`    → `vol[index, :, :]`  — Z axis (slice number)
   - `coronal`  → `vol[:, index, :]`  — Y axis (anterior-posterior)
   - `sagittal` → `vol[:, :, index]`  — X axis (left-right)

5. **dtype** — always cast to `np.int16` before `.tobytes()`. The frontend
   interprets the buffer as a signed 16-bit array.

6. **HU values** — apply `RescaleSlope` and `RescaleIntercept` from each DICOM
   slice before stacking. Stored pixel values without this correction are raw
   detector counts, not HU.

---

## Response assembly in the router

```python
# routers/slices.py
buf, width, height, min_val, max_val = await get_slice_bytes(dataset_id, axis, index, lod)
meta = json.dumps({"width": width, "height": height, "min": min_val, "max": max_val})
return Response(
    content=buf,
    media_type="application/octet-stream",
    headers={"X-Slice-Meta": meta},
)
```

The `lod` parameter is accepted for API compatibility but currently unused
(native resolution only). Add downsampling here when needed — do it in
`_extract_slice` before `.tobytes()` using `scipy.ndimage.zoom` or `skimage.transform.resize`.

---

## What NOT to do

- Do not return JSON with base64-encoded pixels — too large and too slow.
- Do not load the volume inside the async route handler — use `run_in_executor`.
- Do not rely on filename sort order for DICOM slices — always sort by z-position.
- Do not omit `X-Slice-Meta` — the frontend will fail to render without it.
- Do not change the `{"width", "height", "min", "max"}` key names without
  coordinating with the AxiomaUX `LocalDataGateway.ts`.
