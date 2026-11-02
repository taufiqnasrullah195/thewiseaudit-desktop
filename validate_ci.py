"""validate_ci.py — lint .github/workflows/ci.yml structure locally."""
import yaml

with open(".github/workflows/ci.yml", encoding="utf-8") as fh:
    doc = yaml.safe_load(fh)

assert doc["name"] == "CI"
on = doc.get(True) or doc.get("on") or doc.get("True")  # yaml 1.1: bare `on` -> boolean True
assert on and "push" in on and "pull_request" in on
jobs = doc["jobs"]
assert set(jobs) == {"test", "build-windows"}
assert jobs["build-windows"]["needs"] == "test"
matrix = jobs["test"]["strategy"]["matrix"]
assert matrix["os"] == ["ubuntu-latest", "windows-latest"]
assert matrix["python-version"] == ["3.11", "3.12"]
steps = [s.get("name") or s.get("uses") for s in jobs["test"]["steps"]]
assert any("Run tests" in str(s) for s in steps)
assert any("upload-artifact" in str(s.get("uses", ""))
           for s in jobs["build-windows"]["steps"])
print("CI YAML VALID · jobs:", ", ".join(jobs),
      "· matrix:", len(matrix["os"]), "os x",
      len(matrix["python-version"]), "py")
