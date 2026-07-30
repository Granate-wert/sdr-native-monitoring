//! Native, allocation-conscious decoder for one ESW `<SgramLine>` fragment.
//!
//! The Python caller retains all domain semantics (timestamp conversion and
//! NumPy ownership).  This module only scans XML attributes and decodes Base64;
//! malformed input is represented as `None`, so the existing Python decoder is
//! the authoritative compatibility fallback.

use base64::{engine::general_purpose::STANDARD, Engine as _};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

const DATA_BLOCK_OPEN: &[u8] = b"<DataBlock";
const DATA_BLOCK_CLOSE: &[u8] = b"/>";

fn attribute<'a>(tag: &'a [u8], name: &[u8]) -> Option<&'a [u8]> {
    let mut marker = Vec::with_capacity(name.len() + 2);
    marker.extend_from_slice(name);
    marker.extend_from_slice(b"=\"");
    let start = tag.windows(marker.len()).position(|window| window == marker)? + marker.len();
    let end = tag[start..].iter().position(|byte| *byte == b'"')? + start;
    Some(&tag[start..end])
}

fn parse_index(value: Option<&[u8]>) -> Option<usize> {
    let bytes = value.unwrap_or(b"0");
    if bytes.is_empty() {
        return Some(0);
    }
    bytes.iter().try_fold(0usize, |acc, byte| {
        if !byte.is_ascii_digit() {
            return None;
        }
        acc.checked_mul(10)?.checked_add(usize::from(*byte - b'0'))
    })
}

/// Return `(line_index, timestamp_attribute_or_none, decoded_float32_bytes)`.
///
/// `point_count` is deliberately only validated by Python/NumPy, preserving its
/// exact legacy truncation/error behaviour for a malformed final payload.
#[pyfunction]
fn decode_sgram_line<'py>(
    py: Python<'py>,
    blob: &[u8],
    _point_count: usize,
) -> PyResult<Option<(usize, Option<Bound<'py, PyBytes>>, Bound<'py, PyBytes>)>> {
    let header_end = match blob.iter().position(|byte| *byte == b'>') {
        Some(position) => position,
        None => return Ok(None),
    };
    let header = &blob[..=header_end];
    let line_index = match parse_index(attribute(header, b"Line")) {
        Some(value) => value,
        None => return Ok(None),
    };
    let timestamp = attribute(header, b"Timestamp").map(|value| PyBytes::new(py, value));

    let mut blocks: Vec<(usize, Vec<u8>)> = Vec::new();
    let mut position = header_end + 1;
    while let Some(relative_start) = blob[position..]
        .windows(DATA_BLOCK_OPEN.len())
        .position(|window| window == DATA_BLOCK_OPEN)
    {
        let start = position + relative_start;
        let Some(relative_end) = blob[start..]
            .windows(DATA_BLOCK_CLOSE.len())
            .position(|window| window == DATA_BLOCK_CLOSE)
        else {
            break;
        };
        let end = start + relative_end + DATA_BLOCK_CLOSE.len();
        let tag = &blob[start..end];
        if let (Some(block_index), Some(payload)) =
            (parse_index(attribute(tag, b"Block")), attribute(tag, b"Data"))
        {
            if !payload.is_empty() {
                if let Ok(decoded) = STANDARD.decode(payload) {
                    blocks.push((block_index, decoded));
                }
            }
        }
        position = end;
    }

    let raw = match blocks.len() {
        0 => Vec::new(),
        1 => blocks.pop().expect("length checked").1,
        _ => {
            blocks.sort_unstable_by_key(|(index, _)| *index);
            let total = blocks.iter().map(|(_, payload)| payload.len()).sum();
            let mut joined = Vec::with_capacity(total);
            for (_, payload) in blocks {
                joined.extend_from_slice(&payload);
            }
            joined
        }
    };
    if raw.len() % std::mem::size_of::<f32>() != 0 {
        return Err(PyValueError::new_err(
            "Sgram payload length is not float32-aligned",
        ));
    }
    Ok(Some((
        line_index,
        timestamp,
        PyBytes::new(py, &raw),
    )))
}

#[pymodule]
fn _sgram_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(decode_sgram_line, module)?)?;
    Ok(())
}