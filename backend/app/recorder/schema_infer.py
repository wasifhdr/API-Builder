from typing import Any

from genson import SchemaBuilder


def infer_schema(sample: Any) -> dict:
    builder = SchemaBuilder()
    builder.add_object(sample)
    return builder.to_schema()
