#!/usr/bin/env python3

import os
from dataclasses import dataclass
from datetime import date, timedelta
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import TypedDict, Optional
import concurrent.futures

import click
import requests

PUZZLES_API_URL = "https://www.nytimes.com/svc/crosswords/v3/76535102/puzzles.json"
PUZZLE_BASE_URL = "https://www.nytimes.com/svc/crosswords/v2/puzzle"


class Puzzle(TypedDict):
    author: str
    editor: str
    format_type: str
    print_date: str
    publish_type: str
    puzzle_id: int
    title: str
    version: int
    percent_filled: int
    solved: bool
    star: Optional[str]


class PuzzlesApiResponse(TypedDict):
    status: str
    results: list[Puzzle]


@dataclass
class DownloadedPuzzle:
    puzzle: bytes
    solution: bytes


def looks_like_pdf(content: bytes) -> bool:
    return content.startswith(b"%PDF-")


def is_valid_pdf_response(response: requests.Response) -> bool:
    return (
        response.ok
        and response.headers["Content-Type"].startswith("application/pdf")
        and looks_like_pdf(response.content)
    )


def get_puzzle_ids(session: requests.Session, puzzle_date: str) -> list[int]:
    params = {"date_start": puzzle_date, "date_end": puzzle_date}
    try:
        response: PuzzlesApiResponse = session.get(PUZZLES_API_URL, params=params, timeout=30).json()
    except Exception:
        return []
    if response.get("status") != "OK" or not response.get("results"):
        return []
    return [puzzle["puzzle_id"] for puzzle in response["results"]]


def download(session: requests.Session, puzzle_id: int, large_print: bool, left_handed: bool) -> DownloadedPuzzle:
    puzzle_url = f"{PUZZLE_BASE_URL}/{puzzle_id}.pdf"
    soln_url = f"{PUZZLE_BASE_URL}/{puzzle_id}.ans.pdf"
    params = {
        "southpaw": str(left_handed).lower(),
        "large_print": str(large_print).lower(),
    }

    puzzle_response = session.get(puzzle_url, params=params, timeout=30)
    solution_response = session.get(soln_url, timeout=30)

    if not is_valid_pdf_response(puzzle_response):
        raise Exception(f"Invalid PDF for puzzle {puzzle_id}")
    if not is_valid_pdf_response(solution_response):
        raise Exception(f"Invalid PDF for solution {puzzle_id}")

    return DownloadedPuzzle(puzzle=puzzle_response.content, solution=solution_response.content)


def write_pdf(data: bytes, path: os.PathLike) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as pdf:
        pdf.write(data)
    print(f"Wrote {len(data)} bytes to {path}")


def fetch_and_save(session: requests.Session, puzzle_date: str, large_print: bool, left_handed: bool, solution: bool, out_dir: Path):
    puzzle_ids = get_puzzle_ids(session, puzzle_date)
    if not puzzle_ids:
        print(f"No puzzles found for {puzzle_date}")
        return

    for puzzle_id in puzzle_ids:
        try:
            files = download(session, puzzle_id, large_print, left_handed)
            puzzle_file = Path(out_dir, f"{puzzle_date}.pdf")
            write_pdf(files.puzzle, puzzle_file)
            if solution:
                sol_file = Path(out_dir, f"{puzzle_date}.soln.pdf")
                write_pdf(files.solution, sol_file)
        except Exception as e:
            print(f"Error downloading puzzle {puzzle_id} on {puzzle_date}: {e}")


@click.command("nyt-download-all")
@click.option("--start-date", "-s", type=click.STRING, default="1995-02-18", help="Start date (YYYY-MM-DD)")
@click.option("--end-date", "-e", type=click.STRING, default=None, help="End date (YYYY-MM-DD), defaults to today")
@click.option("--large-print/--no-large-print", default=False, help="Fetch large-print variant")
@click.option("--left-handed/--no-left-handed", default=False, help="Fetch left-handed variant")
@click.option("--solution/--no-solution", default=True, help="Download solution PDFs")
@click.option("--cookies", "-b", type=click.Path(exists=True, dir_okay=False, path_type=Path), default="Documents/NYT/cookies.txt")
@click.option("--out-dir", "-o", type=click.Path(exists=False, file_okay=False), default="out")
@click.option("--threads", "-t", type=int, default=20, help="Number of concurrent downloads")
def main(start_date, end_date, large_print, left_handed, solution, cookies, out_dir, threads):
    jar = MozillaCookieJar(cookies)
    jar.load()
    session = requests.Session()
    session.cookies = jar  # type: ignore

    start = date.fromisoformat(start_date)
    end = date.today() if end_date is None else date.fromisoformat(end_date)

    all_dates = []
    current = start
    while current <= end:
        all_dates.append(current.isoformat())
        current += timedelta(days=1)

    print(f"Starting download of {len(all_dates)} days with {threads} threads...")

    out_dir = Path(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [
            executor.submit(fetch_and_save, session, d, large_print, left_handed, solution, out_dir)
            for d in all_dates
        ]
        for future in concurrent.futures.as_completed(futures):
            # catch exceptions from threads
            try:
                future.result()
            except Exception as e:
                print(f"Thread error: {e}")


if __name__ == "__main__":
    main()
