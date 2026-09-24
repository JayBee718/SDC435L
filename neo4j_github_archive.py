"""Menu-driven GitHub Archive application using Python and Neo4j.

The application imports newline-delimited JSON and creates this graph:

    (:Developer)-[:AUTHORED]->(:Commit)-[:BELONGS_TO]->(:Repository)
    (:Developer)-[:COMMITTED]->(:Commit)
    (:Developer)-[:CONTRIBUTED_TO]->(:Repository)

Student ID(s): jerbra1624/davnel2166
"""

import json
from getpass import getpass
from pathlib import Path

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError


DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_DATABASE = "neo4j"
DEFAULT_COMMITS_FILE = "GitHubArchive-Dataset/Sample_Commits.json"
DEFAULT_REPOS_FILE = "GitHubArchive-Dataset/Sample_Repos.json"
BATCH_SIZE = 250


def prompt_nonempty(prompt):
    """Keep prompting until the user supplies text."""
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("A value is required.")


def prompt_integer(prompt, minimum=0):
    """Return a validated whole number."""
    while True:
        try:
            value = int(input(prompt).strip())
            if value >= minimum:
                return value
        except ValueError:
            pass
        print(f"Enter a whole number greater than or equal to {minimum}.")


def connect_to_neo4j():
    """Collect connection settings and verify the Neo4j connection."""
    uri = input(f"Neo4j URI [{DEFAULT_URI}]: ").strip() or DEFAULT_URI
    username = input("Neo4j username [neo4j]: ").strip() or "neo4j"
    # getpass prevents the database password from appearing on the screen.
    password = getpass("Neo4j password: ")
    database = input(f"Database [{DEFAULT_DATABASE}]: ").strip() or DEFAULT_DATABASE

    driver = GraphDatabase.driver(uri, auth=(username, password))
    driver.verify_connectivity()
    return driver, database


def create_schema(driver, database):
    """Create unique constraints so MERGE and record lookup stay reliable."""
    statements = (
        "create constraint commit_hash_unique if not exists "
        "for (c:Commit) require c.hash is unique",
        "create constraint repository_name_unique if not exists "
        "for (r:Repository) require r.name is unique",
        "create constraint developer_email_unique if not exists "
        "for (d:Developer) require d.email is unique",
    )
    with driver.session(database=database) as session:
        for statement in statements:
            session.run(statement).consume()


def import_repository_batch(tx, rows):
    """Write a batch of repository records in one Neo4j transaction."""
    tx.run(
        """
        unwind $rows as row
        merge (r:Repository {name: row.name})
        set r.watch_count = row.watch_count
        """,
        rows=rows,
    ).consume()


def import_commit_batch(tx, rows):
    """Create commit, developer, repository nodes and their relationships."""
    tx.run(
        """
        unwind $rows as row
        merge (r:Repository {name: row.repo_name})
        merge (c:Commit {hash: row.hash})
        set c.subject = row.subject,
            c.message = row.message,
            c.tree = row.tree
        merge (c)-[:BELONGS_TO]->(r)

        merge (a:Developer {email: row.author_email})
        set a.name = row.author_name
        merge (a)-[:AUTHORED]->(c)
        merge (a)-[:CONTRIBUTED_TO]->(r)

        merge (m:Developer {email: row.committer_email})
        set m.name = row.committer_name
        merge (m)-[:COMMITTED]->(c)
        merge (m)-[:CONTRIBUTED_TO]->(r)
        """,
        rows=rows,
    ).consume()


def import_repositories(driver, database, path, limit):
    """Read repository JSONL and add watch counts to Repository nodes."""
    imported = skipped = 0
    batch = []
    with path.open("r", encoding="utf-8") as source:
        with driver.session(database=database) as session:
            for line_number, line in enumerate(source, start=1):
                if limit and imported >= limit:
                    break
                try:
                    record = json.loads(line)
                    row = {
                        "name": str(record["repo_name"]).strip(),
                        "watch_count": int(record.get("watch_count", 0)),
                    }
                    if not row["name"]:
                        raise ValueError("blank repository name")
                    batch.append(row)
                    imported += 1
                    if len(batch) == BATCH_SIZE:
                        session.execute_write(import_repository_batch, batch)
                        batch = []
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    skipped += 1
                    print(f"Skipped invalid repository record on line {line_number}.")
            if batch:
                session.execute_write(import_repository_batch, batch)
    return imported, skipped


def import_commits(driver, database, path, limit):
    """Read commit JSONL and transform each object into connected graph data."""
    imported = skipped = 0
    batch = []
    with path.open("r", encoding="utf-8") as source:
        with driver.session(database=database) as session:
            for line_number, line in enumerate(source, start=1):
                if limit and imported >= limit:
                    break
                try:
                    record = json.loads(line)
                    author = record.get("author") or {}
                    committer = record.get("committer") or author
                    row = {
                        "hash": str(record["commit"]).strip(),
                        "repo_name": str(record["repo_name"]).strip(),
                        "subject": str(record.get("subject", "")).strip(),
                        "message": str(record.get("message", "")).strip(),
                        "tree": str(record.get("tree", "")).strip(),
                        "author_name": str(author.get("name", "Unknown")).strip(),
                        "author_email": str(author.get("email", "")).strip().lower(),
                        "committer_name": str(committer.get("name", "Unknown")).strip(),
                        "committer_email": str(committer.get("email", "")).strip().lower(),
                    }
                    required = (
                        row["hash"], row["repo_name"], row["author_email"],
                        row["committer_email"],
                    )
                    if not all(required):
                        raise ValueError("missing graph identifier")
                    batch.append(row)
                    imported += 1
                    if len(batch) == BATCH_SIZE:
                        session.execute_write(import_commit_batch, batch)
                        batch = []
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    skipped += 1
                    print(f"Skipped invalid commit record on line {line_number}.")
            if batch:
                session.execute_write(import_commit_batch, batch)
    return imported, skipped


def import_json_data(driver, database):
    """Menu action that imports repository metadata followed by commit data."""
    repos_input = input(f"Repository JSON path [{DEFAULT_REPOS_FILE}]: ").strip()
    commits_input = input(f"Commit JSON path [{DEFAULT_COMMITS_FILE}]: ").strip()
    repos_path = Path(repos_input or DEFAULT_REPOS_FILE).expanduser()
    commits_path = Path(commits_input or DEFAULT_COMMITS_FILE).expanduser()

    if not repos_path.is_file() or not commits_path.is_file():
        print("One or both files were not found. Extract the supplied ZIP first.")
        return

    limit = prompt_integer("Maximum commits to import (0 = all): ")
    # Import enough repository rows to enrich any repository created by commits.
    repo_limit = 0 if limit == 0 else max(limit, 1000)
    repos_saved, repos_skipped = import_repositories(
        driver, database, repos_path, repo_limit
    )
    commits_saved, commits_skipped = import_commits(
        driver, database, commits_path, limit
    )
    print(
        f"Import complete: {repos_saved} repositories and {commits_saved} commits "
        f"saved; {repos_skipped + commits_skipped} invalid records skipped."
    )


def create_commit(driver, database):
    """Create one commit and connect it to a developer and repository."""
    data = {
        "hash": prompt_nonempty("Commit hash or unique demo ID: "),
        "repo_name": prompt_nonempty("Repository (owner/name): "),
        "subject": prompt_nonempty("Commit subject: "),
        "message": input("Commit message: ").strip(),
        "author_name": prompt_nonempty("Author name: "),
        "author_email": prompt_nonempty("Author email: ").lower(),
    }
    query = """
    merge (r:Repository {name: $repo_name})
    merge (d:Developer {email: $author_email})
    set d.name = $author_name
    merge (c:Commit {hash: $hash})
    set c.subject = $subject, c.message = $message
    merge (d)-[:AUTHORED]->(c)
    merge (d)-[:COMMITTED]->(c)
    merge (c)-[:BELONGS_TO]->(r)
    merge (d)-[:CONTRIBUTED_TO]->(r)
    return c.hash as hash
    """
    with driver.session(database=database) as session:
        session.run(query, **data).consume()
    print("Commit and graph relationships saved.")


def read_commit(driver, database):
    """Retrieve a commit with its repository, author, and committer."""
    commit_hash = prompt_nonempty("Exact commit hash or demo ID: ")
    query = """
    match (c:Commit {hash: $hash})-[:BELONGS_TO]->(r:Repository)
    optional match (a:Developer)-[:AUTHORED]->(c)
    optional match (m:Developer)-[:COMMITTED]->(c)
    return c.hash as hash, c.subject as subject, c.message as message,
           r.name as repository, a.name as author, a.email as author_email,
           m.name as committer
    """
    with driver.session(database=database) as session:
        record = session.run(query, hash=commit_hash).single()
    if not record:
        print("Commit not found.")
        return
    print(f"\nHash: {record['hash']}")
    print(f"Repository: {record['repository']}")
    print(f"Author: {record['author']} <{record['author_email']}>")
    print(f"Committer: {record['committer'] or 'Not recorded'}")
    print(f"Subject: {record['subject']}")
    print(f"Message: {record['message']}")


def update_commit(driver, database):
    """Update the subject and message of an existing commit."""
    commit_hash = prompt_nonempty("Exact commit hash or demo ID to update: ")
    subject = prompt_nonempty("New subject: ")
    message = input("New message: ").strip()
    query = """
    match (c:Commit {hash: $hash})
    set c.subject = $subject, c.message = $message
    return count(c) as changed
    """
    with driver.session(database=database) as session:
        changed = session.run(
            query, hash=commit_hash, subject=subject, message=message
        ).single()["changed"]
    print("Commit updated." if changed else "Commit not found; nothing was updated.")


def delete_commit(driver, database):
    """Delete one commit while leaving shared developer and repository nodes."""
    commit_hash = prompt_nonempty("Exact commit hash or demo ID to delete: ")
    if input("Type DELETE to confirm: ").strip() != "DELETE":
        print("Delete canceled.")
        return
    query = """
    match (c:Commit {hash: $hash})
    with c, count(c) as found
    detach delete c
    return found
    """
    with driver.session(database=database) as session:
        record = session.run(query, hash=commit_hash).single()
    print("Commit deleted." if record else "Commit not found.")


def contributor_leaderboard(driver, database):
    """Feature 1: rank authors by commits and repository reach."""
    limit = prompt_integer("Number of contributors to display: ", 1)
    query = """
    match (d:Developer)-[:AUTHORED]->(c:Commit)-[:BELONGS_TO]->(r:Repository)
    return d.name as developer, d.email as email, count(c) as commits,
           count(distinct r) as repositories
    order by commits desc, repositories desc, developer
    limit $limit
    """
    with driver.session(database=database) as session:
        rows = list(session.run(query, limit=limit))
    print("\nContributor leaderboard")
    for number, row in enumerate(rows, start=1):
        print(
            f"{number:>2}. {row['developer']} - {row['commits']} commits across "
            f"{row['repositories']} repositories"
        )


def repository_constellation(driver, database):
    """Feature 2: show a repository's graph neighborhood."""
    name = prompt_nonempty("Exact repository name: ")
    query = """
    match (r:Repository {name: $name})
    optional match (d:Developer)-[:AUTHORED]->(c:Commit)-[:BELONGS_TO]->(r)
    with r, count(distinct c) as commits, count(distinct d) as contributors,
         collect(distinct d.name)[0..8] as people,
         collect(distinct c.subject)[0..5] as subjects
    return r.name as repository, r.watch_count as watches, commits,
           contributors, people, subjects
    """
    with driver.session(database=database) as session:
        row = session.run(query, name=name).single()
    if not row:
        print("Repository not found.")
        return
    print(f"\nRepository constellation: {row['repository']}")
    print(f"Watches: {row['watches'] if row['watches'] is not None else 'Unknown'}")
    print(f"Imported commits: {row['commits']}")
    print(f"Contributors: {row['contributors']}")
    print("People: " + (", ".join(row["people"]) or "None in imported sample"))
    print("Recent subjects:")
    for subject in row["subjects"]:
        print(f"  - {subject}")


def collaborator_recommendations(driver, database):
    """Feature 3: recommend developers based on repositories in common."""
    email = prompt_nonempty("Developer email: ").lower()
    query = """
    match (me:Developer {email: $email})-[:CONTRIBUTED_TO]->(r:Repository)
          <-[:CONTRIBUTED_TO]-(other:Developer)
    where other <> me
    return other.name as developer, other.email as email,
           count(distinct r) as shared_repositories,
           collect(distinct r.name)[0..5] as examples
    order by shared_repositories desc, developer
    limit 10
    """
    with driver.session(database=database) as session:
        rows = list(session.run(query, email=email))
    if not rows:
        print("No collaborator recommendations were found in the imported sample.")
        return
    print("\nSuggested collaborators")
    for row in rows:
        print(
            f"- {row['developer']} ({row['email']}): "
            f"{row['shared_repositories']} shared repos — {', '.join(row['examples'])}"
        )


def developer_connection_path(driver, database):
    """Feature 4: find the shortest shared-repository path between developers."""
    first = prompt_nonempty("First developer email: ").lower()
    second = prompt_nonempty("Second developer email: ").lower()
    query = """
    match (a:Developer {email: $first}), (b:Developer {email: $second})
    match path = shortestPath((a)-[:CONTRIBUTED_TO*..6]-(b))
    return [node in nodes(path) |
        case when node:Developer then coalesce(node.name, node.email)
             else node.name end] as connection
    """
    with driver.session(database=database) as session:
        row = session.run(query, first=first, second=second).single()
    if not row:
        print("No connection was found within three repository hops.")
        return
    print("Connection path: " + " -> ".join(row["connection"]))


def search_commit_messages(driver, database):
    """Feature 5: search subjects and messages for a case-insensitive keyword."""
    keyword = prompt_nonempty("Commit keyword: ").lower()
    query = """
    match (c:Commit)-[:BELONGS_TO]->(r:Repository)
    where toLower(coalesce(c.subject, '')) contains $keyword
       or toLower(coalesce(c.message, '')) contains $keyword
    return c.hash as hash, c.subject as subject, r.name as repository
    order by repository, subject
    limit 20
    """
    with driver.session(database=database) as session:
        rows = list(session.run(query, keyword=keyword))
    if not rows:
        print("No matching commits were found.")
        return
    for row in rows:
        print(f"- [{row['hash'][:10]}] {row['repository']}: {row['subject']}")


def show_menu():
    """Display all application actions."""
    print(
        "\nGitHub Archive Neo4j Explorer\n"
        "1. Import GitHub Archive JSON\n"
        "2. Create a commit\n"
        "3. Read a commit\n"
        "4. Update a commit\n"
        "5. Delete a commit\n"
        "6. Contributor leaderboard\n"
        "7. Repository constellation\n"
        "8. Collaborator recommendations\n"
        "9. Developer connection path\n"
        "10. Search commit messages\n"
        "11. Exit"
    )


def main():
    """Connect to Neo4j and run the terminal menu until the user exits."""
    try:
        driver, database = connect_to_neo4j()
        create_schema(driver, database)
    except Exception as error:
        print(f"Could not connect to Neo4j: {error}")
        print("Confirm Neo4j is running and that your URI, username, and password are correct.")
        return

    actions = {
        "1": import_json_data,
        "2": create_commit,
        "3": read_commit,
        "4": update_commit,
        "5": delete_commit,
        "6": contributor_leaderboard,
        "7": repository_constellation,
        "8": collaborator_recommendations,
        "9": developer_connection_path,
        "10": search_commit_messages,
    }

    try:
        while True:
            show_menu()
            choice = input("Choose an option: ").strip()
            if choice == "11":
                print("Goodbye!")
                break
            action = actions.get(choice)
            if not action:
                print("Choose an option from 1 through 11.")
                continue
            try:
                action(driver, database)
            except (Neo4jError, OSError) as error:
                print(f"The operation could not be completed: {error}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
