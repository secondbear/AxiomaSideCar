import pydicom

import services.structure_service as structure_service


def _fake_rtstruct():
    roi = pydicom.Dataset()
    roi.ROINumber = 1
    roi.ROIName = "CTV"

    contour = pydicom.Dataset()
    contour.ContourGeometricType = "CLOSED_PLANAR"
    contour.ContourData = [0.0, 0.0, 10.0, 1.0, 0.0, 10.0, 1.0, 1.0, 10.0]

    roi_contour = pydicom.Dataset()
    roi_contour.ReferencedROINumber = 1
    roi_contour.ROIDisplayColor = [255, 0, 0]
    roi_contour.ContourSequence = [contour]

    rtstruct = pydicom.Dataset()
    rtstruct.StructureSetROISequence = [roi]
    rtstruct.ROIContourSequence = [roi_contour]
    return rtstruct


def test_parse_rtstruct_metadata_and_contours(monkeypatch):
    monkeypatch.setattr(
        structure_service,
        "_find_rtstruct",
        lambda items: (None, _fake_rtstruct()),
    )
    monkeypatch.setattr(structure_service, "_read_ct_slice_positions", lambda items: [10.0])

    structures = structure_service._parse_structures("dataset-1", [])
    contours = structure_service._parse_contours("dataset-1", "dataset-1:1", 0, [])

    assert structures == [
        {
            "id": "dataset-1:1",
            "dataset_id": "dataset-1",
            "roi_number": 1,
            "name": "CTV",
            "color": [255, 0, 0],
            "contour_count": 1,
        }
    ]
    assert contours[0]["slice_index"] == 0
    assert contours[0]["geometric_type"] == "CLOSED_PLANAR"
    assert contours[0]["points"][0] == [0.0, 0.0, 10.0]


async def test_structures_unknown_dataset(client):
    response = await client.get("/api/v1/datasets/missing/structures")

    assert response.status_code == 404
