def run(IN):
    try:
        return {"output": f"Hello, {IN[0]}!"}
    except Exception as e:
        return {"error": str(e)}
