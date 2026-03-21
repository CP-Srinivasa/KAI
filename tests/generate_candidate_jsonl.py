import json


def main():
    with open("out_teacher.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    for i, row in enumerate(rows):
        # Slightly perturb priorities and impacts to have imperfect MAE
        target = json.loads(row["messages"][2]["content"])

        target["priority_score"] = float(target.get("priority_score", 5)) - (1 if i % 2 == 0 else 0)
        target["impact_score"] = float(target.get("impact_score", 0.5)) - (0.1 if i % 2 == 1 else 0)

        row["messages"][2]["content"] = json.dumps(target)
        row["metadata"]["provider"] = "companion"
        row["metadata"]["analysis_source"] = "internal"

    with open("out_candidate.jsonl", "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

if __name__ == "__main__":
    main()
