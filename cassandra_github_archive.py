"""
Name: Jerald Bradley
Date: September 16, 2026
Assignment: Cassandra Intergration
Purpose: Python terminal application imports repository records from the supplied
GitHub Archive dataset and stores them in Apache Cassandra

"""

import json
from collections import Counter
from heapq import nlargest
from pathlib import Path

from cassandra.cluster import Cluster
from cassandra.query import BatchStatement


KEYSPACE = "github_archive"
TABLE = "repositories"
DEFAULT_JSON = "GitHubArchive-Dataset/Sample_Repos.json"


def connect_to_cassandra():
    """Connect to local Cassandra and create the required schema."""
    cluster = Cluster(["127.0.0.1"])
    session = cluster.connect()
    session.execute(
        f"""
        create keyspace if not exists {KEYSPACE}
        with replication = {{'class': 'SimpleStrategy', 'replication_factor': 1}}
        """
    )
    session.set_keyspace(KEYSPACE)
    session.execute(
        f"""
        create table if not exists {TABLE} (
            repo_name text primary key,
            watch_count int
        )
        """
    )
    return cluster, session


def read_integer(prompt, minimum=0):
    """Read and validate a whole number from the keyboard."""
    while True:
        try:
            value = int(input(prompt).strip())
            if value >= minimum:
                return value
        except ValueError:
            pass
        print(f"Please enter a whole number greater than or equal to {minimum}.")


def import_json(session):
    """Import newline-delimited repository JSON with efficient small batches."""
    raw_path = input(f"JSON path [{DEFAULT_JSON}]: ").strip()
    path = Path(raw_path or DEFAULT_JSON).expanduser()
    if not path.is_file():
        print("File not found. Enter the full path or extract the supplied ZIP first.")
        return

    limit = read_integer("Maximum records to import (0 = all): ")
    prepared = session.prepare(
        f"insert into {TABLE} (repo_name, watch_count) values (?, ?)"
    )
    imported = skipped = 0
    batch = BatchStatement()

    with path.open("r", encoding="utf-8") as json_file:
        for line_number, line in enumerate(json_file, start=1):
            if limit and imported >= limit:
                break
            try:
                record = json.loads(line)
                repo_name = str(record["repo_name"]).strip()
                watch_count = int(record.get("watch_count", 0))
                if not repo_name:
                    raise ValueError("empty repository name")
                batch.add(prepared, (repo_name, watch_count))
                imported += 1
                if len(batch) >= 50:
                    session.execute(batch)
                    batch = BatchStatement()
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                skipped += 1
                print(f"Skipped invalid record on line {line_number}.")

    if len(batch):
        session.execute(batch)
    print(f"Import complete: {imported} saved and {skipped} skipped.")


def create_repository(session):
    """Create a repository record or replace one with the same name."""
    name = input("Repository name (owner/repository): ").strip()
    if not name:
        print("Repository name cannot be blank.")
        return
    watches = read_integer("Watch count: ")
    session.execute(
        f"insert into {TABLE} (repo_name, watch_count) values (%s, %s)",
        (name, watches),
    )
    print("Repository saved.")


def read_repository(session):
    """Retrieve one record by its Cassandra partition key."""
    name = input("Exact repository name: ").strip()
    row = session.execute(
        f"select repo_name, watch_count from {TABLE} where repo_name = %s", (name,)
    ).one()
    if row:
        print(f"Repository: {row.repo_name}\nWatch count: {row.watch_count}")
    else:
        print("Repository not found.")


def update_repository(session):
    """Update the watch count for an existing repository."""
    name = input("Exact repository name to update: ").strip()
    exists = session.execute(
        f"select repo_name from {TABLE} where repo_name = %s", (name,)
    ).one()
    if not exists:
        print("Repository not found; no update was made.")
        return
    watches = read_integer("New watch count: ")
    session.execute(
        f"update {TABLE} set watch_count = %s where repo_name = %s", (watches, name)
    )
    print("Repository updated.")


def delete_repository(session):
    """Delete one repository after receiving confirmation."""
    name = input("Exact repository name to delete: ").strip()
    confirmation = input(f"Type DELETE to remove {name}: ").strip()
    if confirmation != "DELETE":
        print("Delete canceled.")
        return
    session.execute(f"delete from {TABLE} where repo_name = %s", (name,))
    print("Delete command completed.")


def get_all_repositories(session):
    """Return repository rows for the coursework-sized analytical features."""
    return list(session.execute(f"select repo_name, watch_count from {TABLE}"))


def show_name_survey(session):
    """Feature 1: display the shortest and longest repository names."""
    rows = get_all_repositories(session)
    if not rows:
        print("No repository data is available.")
        return
    shortest = min(rows, key=lambda row: (len(row.repo_name), row.repo_name.lower()))
    longest = max(rows, key=lambda row: (len(row.repo_name), row.repo_name.lower()))
    print(f"Shortest ({len(shortest.repo_name)} characters): {shortest.repo_name}")
    print(f"Longest  ({len(longest.repo_name)} characters): {longest.repo_name}")


def show_watch_distribution(session):
    """Feature 2: summarize repositories by watch-count range."""
    rows = get_all_repositories(session)
    if not rows:
        print("No repository data is available.")
        return
    ranges = Counter()
    for row in rows:
        count = row.watch_count or 0
        if count < 100:
            ranges["0-99"] += 1
        elif count < 1000:
            ranges["100-999"] += 1
        elif count < 10000:
            ranges["1,000-9,999"] += 1
        else:
            ranges["10,000+"] += 1
    print("\nWatch-count distribution")
    for label in ("0-99", "100-999", "1,000-9,999", "10,000+"):
        print(f"{label:>12}: {ranges[label]}")


def show_top_repositories(session):
    """Feature 3: display the repositories with the highest watch counts."""
    rows = get_all_repositories(session)
    if not rows:
        print("No repository data is available.")
        return
    quantity = read_integer("How many repositories should be displayed? ", 1)
    print("\nMost-watched repositories")
    for position, row in enumerate(
        nlargest(quantity, rows, key=lambda item: item.watch_count or 0), start=1
    ):
        print(f"{position:>2}. {row.repo_name} - {row.watch_count:,} watches")


def main():
    """Run the menu until the user chooses to exit."""
    try:
        cluster, session = connect_to_cassandra()
    except Exception as error:
        print(f"Could not connect to Cassandra: {error}")
        print("Confirm that Cassandra is running on 127.0.0.1:9042.")
        return

    actions = {
        "1": import_json,
        "2": create_repository,
        "3": read_repository,
        "4": update_repository,
        "5": delete_repository,
        "6": show_name_survey,
        "7": show_watch_distribution,
        "8": show_top_repositories,
    }

    try:
        while True:
            print(
                "\nGitHub Archive Cassandra Application\n"
                "1. Import repository JSON\n"
                "2. Create a repository\n"
                "3. Read a repository\n"
                "4. Update a repository\n"
                "5. Delete a repository\n"
                "6. Survey repository-name lengths\n"
                "7. Display watch-count distribution\n"
                "8. Display most-watched repositories\n"
                "9. Exit"
            )
            choice = input("Choose an option: ").strip()
            if choice == "9":
                print("Goodbye!")
                break
            action = actions.get(choice)
            if action:
                action(session)
            else:
                print("Please choose an option from 1 through 9.")
    finally:
        cluster.shutdown()


if __name__ == "__main__":
    main()
