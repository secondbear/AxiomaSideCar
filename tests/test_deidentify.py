import asyncio

import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from tests.conftest import _create_patient, _create_session


async def _wait_completed(client, job_id: str) -> dict:
    for _ in range(50):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        body = response.json()
        if body["status"] not in {"queued", "running"}:
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("deidentify job did not finish")


def _write_dicom(path):
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    dataset.PatientName = "Sensitive^Patient"
    dataset.PatientID = "MRN-123"
    dataset.PatientBirthDate = "19700101"
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = generate_uid()
    dataset.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    dataset.is_little_endian = True
    dataset.is_implicit_VR = False
    dataset.save_as(path)


def _write_referencing_dicom(path, referenced_sop_uid):
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    dataset.PatientName = "Sensitive^Patient"
    dataset.PatientID = "MRN-123"
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = generate_uid()
    dataset.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    dataset.ReferencedImageSequence = [pydicom.Dataset()]
    dataset.ReferencedImageSequence[0].ReferencedSOPInstanceUID = referenced_sop_uid
    dataset.is_little_endian = True
    dataset.is_implicit_VR = False
    dataset.save_as(path)


async def test_deidentify_job_removes_identifiers(client, tmp_path):
    patient_id = await _create_patient(client)
    session_id = await _create_session(client, patient_id)
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    _write_dicom(source / "image.dcm")

    response = await client.post(
        f"/api/v1/sessions/{session_id}/jobs",
        json={
            "type": "deidentify",
            "params": {"source_dir": str(source), "output_dir": str(output)},
        },
    )
    assert response.status_code == 202

    final = await _wait_completed(client, response.json()["id"])
    assert final["status"] == "completed"
    assert final["result"]["file_count"] == 1

    anonymized = pydicom.dcmread(output / "image.dcm")
    assert anonymized.PatientName == "ANONYMOUS"
    assert anonymized.PatientID.startswith("ANON-")
    assert not hasattr(anonymized, "PatientBirthDate")
    assert (output / "deidentification-manifest.json").is_file()


async def test_deidentify_job_remaps_cross_file_uid_references(client, tmp_path):
    patient_id = await _create_patient(client)
    session_id = await _create_session(client, patient_id)
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()

    referenced_uid = generate_uid()
    _write_dicom(source / "image.dcm")
    image = pydicom.dcmread(source / "image.dcm")
    image.SOPInstanceUID = referenced_uid
    image.file_meta.MediaStorageSOPInstanceUID = referenced_uid
    image.save_as(source / "image.dcm")
    _write_referencing_dicom(source / "reference.dcm", referenced_uid)

    response = await client.post(
        f"/api/v1/sessions/{session_id}/jobs",
        json={
            "type": "deidentify",
            "params": {"source_dir": str(source), "output_dir": str(output)},
        },
    )
    final = await _wait_completed(client, response.json()["id"])
    assert final["status"] == "completed"

    anonymized_image = pydicom.dcmread(output / "image.dcm")
    anonymized_reference = pydicom.dcmread(output / "reference.dcm")
    assert anonymized_image.SOPInstanceUID != referenced_uid
    assert (
        anonymized_reference.ReferencedImageSequence[0].ReferencedSOPInstanceUID
        == anonymized_image.SOPInstanceUID
    )
