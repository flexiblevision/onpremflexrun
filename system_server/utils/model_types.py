"""
The device's model-type vocabulary.

Three names for one idea: wire type ('high_accuracy'), fvonprem.models bucket
('versions'), cloud /models/versions key ('models'). Only high accuracy differs.
"""

HIGH_ACCURACY = 'high_accuracy'
HIGH_SPEED    = 'high_speed'
OCR           = 'ocr'
ANOMALY       = 'anomaly'
WAVEFORM      = 'waveform'

DEVICE_BUCKET = {
    HIGH_ACCURACY: 'versions',
    HIGH_SPEED:    'high_speed',
    OCR:           'ocr',
    ANOMALY:       'anomaly',
    WAVEFORM:      'waveform',
}

CLOUD_KEY = {
    HIGH_ACCURACY: 'models',
    HIGH_SPEED:    'lite',
    OCR:           'ocr',
    ANOMALY:       'anomaly',
    WAVEFORM:      'waveform',
}

# Types retrieve_models can install. The rest have their own workers.
DETECTION_TYPES = [HIGH_ACCURACY, HIGH_SPEED, OCR]

LITE_MODEL_TYPES = [HIGH_SPEED]

ALL_TYPES = list(DEVICE_BUCKET.keys())


def is_known(model_type):
    return model_type in DEVICE_BUCKET


def bucket_for(model_type):
    return DEVICE_BUCKET[model_type]


def resolve(model_type):
    """Absent means high accuracy; unknown is returned as-is, never coerced."""
    return HIGH_ACCURACY if not model_type else model_type


def handled_by_retrieve_models(model_type):
    return resolve(model_type) in DETECTION_TYPES
