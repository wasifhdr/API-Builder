class ParamCoercionError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def coerce_params(parameters: list[dict], raw: dict[str, str]) -> dict:
    result: dict = {}
    errors: list[str] = []

    for p in parameters:
        name = p["name"]
        ptype = p.get("type", "string")
        value = raw.get(name)

        if value is None or value == "":
            if p.get("required", True):
                errors.append(f"missing required parameter: {name}")
            continue

        try:
            if ptype == "integer":
                result[name] = int(value)
            elif ptype == "number":
                result[name] = float(value)
            elif ptype == "boolean":
                result[name] = value.strip().lower() in ("1", "true", "yes")
            else:
                result[name] = value
        except ValueError:
            errors.append(f"parameter '{name}' must be of type {ptype}")

    if errors:
        raise ParamCoercionError(errors)
    return result
