"""검색 품질 측정 — 실제 서버 API(업로드 → 유사 검색)로 질문 세트를 채점한다.

사용법 (로그인이 꺼진 측정용 서버를 따로 띄운 뒤):
    python eval/run_eval.py upload --base http://127.0.0.1:8001 --docs <문서 폴더>
    python eval/run_eval.py query  --base http://127.0.0.1:8001 --queries <queries.json> --out <결과.json>

queries.json 형식:
    [{"id": "q01", "q": "질문", "targets": ["정답 파일명", ...], "kind": "paraphrase"}, ...]
    targets 중 하나라도 결과에 나오면 그 순위를 정답 순위로 본다.

지표:
    hit@1, hit@5  — 정답이 1위 / 5위 안에 든 질문 비율 (PRD 목표: hit@5 80% 이상)
    MRR@10        — 정답 순위의 역수 평균(10위 밖이면 0)

측정용 문서와 질문은 저장소에 올리지 않는다(팀원·학교 문서가 섞여 있어서). 이 스크립트만 공유한다.
"""

import argparse
import collections
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def _request(url, data=None, headers=None, timeout=600):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _multipart(path: Path):
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode("utf-8")
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def _wait_parsed(base, ids, timeout):
    started = time.time()
    while True:
        q = urllib.parse.urlencode({"ids": ",".join(map(str, ids))})
        rows = _request(f"{base}/documents/statuses?{q}")
        if not [r for r in rows if r["status"] == "parsing"]:
            return rows
        if time.time() - started > timeout:
            sys.exit(f"timeout: documents still parsing ({ids})")
        time.sleep(3)


def cmd_upload(args):
    docs = sorted(p for p in Path(args.docs).iterdir() if p.is_file())
    ids = {}
    started = time.time()
    for p in docs:
        body, headers = _multipart(p)
        try:
            r = _request(f"{args.base}/upload", body, headers)
        except urllib.error.HTTPError as e:   # 지원하지 않는 형식 등 — 거부된 채로 측정을 이어간다
            print(f"REJECTED {e.code} {p.name}: {e.read().decode('utf-8', 'ignore')[:120]}")
            continue
        if "id" not in r:                     # 업로드 단계 거부는 200 + status=failed로 온다
            print(f"REJECTED {p.name}: {r.get('error')}")
            continue
        ids[r["id"]] = p.name
        print(f"uploaded {r['id']:3d} {r['status']:8s} {p.name}")
        if args.sequential:   # 한 문서의 파싱이 끝난 뒤 다음 문서를 올린다
            _wait_parsed(args.base, [r["id"]], args.timeout)

    rows = _wait_parsed(args.base, list(ids), args.timeout)
    failed = [r for r in rows if r["status"] != "success"]
    print(f"\nparsed {len(rows) - len(failed)}/{len(rows)} in {time.time() - started:.0f}s")
    for r in failed:
        print(f"  FAILED {ids[r['id']]}: {r.get('error')}")
    for r in rows:
        if r.get("has_flagged"):
            print(f"  flagged(OCR 확인 필요) {ids[r['id']]}")


def cmd_query(args):
    queries = json.loads(Path(args.queries).read_text(encoding="utf-8"))
    rows, latencies = [], []
    for item in queries:
        url = f"{args.base}/search?" + urllib.parse.urlencode({"q": item["q"], "mode": "semantic"})
        t0 = time.perf_counter()
        res = _request(url)
        latencies.append(time.perf_counter() - t0)
        ranked = []
        for r in res.get("results", []):
            if r["filename"] not in ranked:
                ranked.append(r["filename"])
        rank = next((i + 1 for i, name in enumerate(ranked) if name in item["targets"]), None)
        rows.append({**item, "rank": rank, "top3": ranked[:3], "engine_down": res.get("engine_down")})

    def summary(subset):
        n = len(subset)
        if n == 0:
            return None
        hit1 = sum(1 for r in subset if r["rank"] == 1) / n
        hit5 = sum(1 for r in subset if r["rank"] and r["rank"] <= 5) / n
        mrr = sum(1 / r["rank"] for r in subset if r["rank"] and r["rank"] <= 10) / n
        return {"n": n, "hit@1": round(hit1, 3), "hit@5": round(hit5, 3), "mrr@10": round(mrr, 3)}

    by_kind = collections.defaultdict(list)
    by_fmt = collections.defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r)
        by_fmt[Path(r["targets"][0]).suffix.lower().lstrip(".")].append(r)
    if any(r["targets"][0].startswith("scan_") for r in rows):
        by_fmt["scan-pdf"] = [r for r in rows if r["targets"][0].startswith("scan_")]
        by_fmt["pdf"] = [r for r in by_fmt["pdf"] if not r["targets"][0].startswith("scan_")]

    report = {
        "label": args.label,
        "overall": summary(rows),
        "by_kind": {k: summary(v) for k, v in sorted(by_kind.items())},
        "by_target_format": {k: summary(v) for k, v in sorted(by_fmt.items())},
        "latency_sec": {"mean": round(sum(latencies) / len(latencies), 3),
                        "max": round(max(latencies), 3)},
        "engine_down_any": any(r["engine_down"] for r in rows),
        "queries": rows,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{args.label}] overall {report['overall']}  latency {report['latency_sec']}")
    for k, v in report["by_kind"].items():
        print(f"  kind   {k:13s} {v}")
    for k, v in report["by_target_format"].items():
        print(f"  format {k:13s} {v}")
    misses = [r for r in rows if not r["rank"] or r["rank"] > 5]
    print(f"  misses (not in top5): {len(misses)}")
    for r in misses:
        print(f"    {r['id']} rank={r['rank']} q={r['q'][:40]!r} top3={r['top3']}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upload")
    up.add_argument("--base", required=True)
    up.add_argument("--docs", required=True)
    up.add_argument("--timeout", type=int, default=7200)
    up.add_argument("--sequential", action="store_true",
                    help="한 문서씩 파싱 완료를 기다리며 올린다(측정 조건을 같게 맞출 때)")
    qu = sub.add_parser("query")
    qu.add_argument("--base", required=True)
    qu.add_argument("--queries", required=True)
    qu.add_argument("--out")
    qu.add_argument("--label", default="run")
    args = ap.parse_args()
    {"upload": cmd_upload, "query": cmd_query}[args.cmd](args)


if __name__ == "__main__":
    main()
