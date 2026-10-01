"""GitHub Archive SQLite terminal application.

Name(s): Jerald Bradley, David Nelson, Tracey Scott

Week 5 Project: Python Application with SQLite Integration

This program reads JSON Lines data directly from the supplied GitHub Archive
ZIP file. It stores repositories, languages, and commits in related SQLite
tables, performs CRUD operations, and provides several analysis features.
"""

import csv
import json
import sqlite3
import zipfile
from pathlib import Path


DATABASE_FILE = "github_archive.db"
DEFAULT_ZIP_FILE = "GitHubArchive-Dataset.zip"
BATCH_SIZE = 500


class GitHubSQLiteApp:
    """Menu-driven application for managing GitHub Archive data in SQLite."""

    def __init__(self, database_file=DATABASE_FILE):
        # sqlite3 is included with Python, so no external database driver is needed.
        self.connection = sqlite3.connect(database_file)
        self.connection.row_factory = sqlite3.Row

        # Foreign keys must be enabled for each SQLite connection.
        self.connection.execute("pragma foreign_keys = on")
        self.create_schema()

    def create_schema(self):
        """Create the related tables and indexes when they do not yet exist."""
        self.connection.executescript(
            """
            create table if not exists repositories (
                repository_id integer primary key autoincrement,
                repo_name text not null unique,
                description text default '',
                watch_count integer not null default 0 check (watch_count >= 0),
                source text not null default 'user',
                created_at text not null default current_timestamp
            );

            create table if not exists languages (
                language_id integer primary key autoincrement,
                repository_id integer not null,
                language_name text not null,
                byte_count integer not null default 0 check (byte_count >= 0),
                foreign key (repository_id)
                    references repositories(repository_id) on delete cascade,
                unique (repository_id, language_name)
            );

            create table if not exists commits (
                commit_hash text primary key,
                repository_id integer not null,
                author_name text not null default 'Unknown',
                author_email text default '',
                subject text default '',
                message text default '',
                imported_at text not null default current_timestamp,
                foreign key (repository_id)
                    references repositories(repository_id) on delete cascade
            );

            create index if not exists idx_commits_repository
                on commits(repository_id);
            create index if not exists idx_commits_author
                on commits(author_name);
            create index if not exists idx_languages_name
                on languages(language_name);
            """
        )
        self.connection.commit()

    @staticmethod
    def prompt_required(message):
        """Continue prompting until the user enters a nonblank value."""
        while True:
            value = input(message).strip()
            if value:
                return value
            print("A value is required.")

    @staticmethod
    def prompt_integer(message, minimum=0, allow_blank=False):
        """Read a validated whole number from the keyboard."""
        while True:
            value = input(message).strip()
            if allow_blank and not value:
                return None
            try:
                number = int(value)
                if number >= minimum:
                    return number
            except ValueError:
                pass
            print(f"Enter a whole number greater than or equal to {minimum}.")

    # ---------------------------- CRUD OPERATIONS ----------------------------

    def create_repository(self):
        """CREATE: Insert a new repository record using parameters."""
        name = self.prompt_required("Repository name (owner/repository): ")
        description = input("Description: ").strip()
        watches = self.prompt_integer("Watch count: ")

        try:
            self.connection.execute(
                """
                insert into repositories
                    (repo_name, description, watch_count, source)
                values (?, ?, ?, 'user')
                """,
                (name, description, watches),
            )
            self.connection.commit()
            print(f"Created '{name}'.")
        except sqlite3.IntegrityError:
            print("That repository already exists. Use Update instead.")

    def read_repository(self):
        """READ: Display one repository and summary data from related tables."""
        name = self.prompt_required("Exact repository name: ")
        row = self.connection.execute(
            """
            select r.repo_name, r.description, r.watch_count, r.source,
                   count(distinct c.commit_hash) as commit_count,
                   count(distinct l.language_id) as language_count
            from repositories r
            left join commits c on c.repository_id = r.repository_id
            left join languages l on l.repository_id = r.repository_id
            where r.repo_name = ? collate nocase
            group by r.repository_id
            """,
            (name,),
        ).fetchone()

        if not row:
            print("Repository not found.")
            return

        print("\nRepository record")
        print(f"Name: {row['repo_name']}")
        print(f"Description: {row['description'] or '(No description)'}")
        print(f"Watch count: {row['watch_count']:,}")
        print(f"Imported commits: {row['commit_count']:,}")
        print(f"Recorded languages: {row['language_count']:,}")
        print(f"Source: {row['source']}")

    def update_repository(self):
        """UPDATE: Change selected values while preserving blank fields."""
        name = self.prompt_required("Exact repository name to update: ")
        current = self.connection.execute(
            "select * from repositories where repo_name = ? collate nocase",
            (name,),
        ).fetchone()
        if not current:
            print("Repository not found.")
            return

        print("Press Enter to keep the current value.")
        description = input(f"Description [{current['description']}]: ").strip()
        watches = self.prompt_integer(
            f"Watch count [{current['watch_count']}]: ", allow_blank=True
        )

        self.connection.execute(
            """
            update repositories
            set description = ?, watch_count = ?
            where repository_id = ?
            """,
            (
                description if description else current["description"],
                watches if watches is not None else current["watch_count"],
                current["repository_id"],
            ),
        )
        self.connection.commit()
        print(f"Updated '{current['repo_name']}'.")

    def delete_repository(self):
        """DELETE: Remove a repository after showing its cascade impact."""
        name = self.prompt_required("Exact repository name to delete: ")
        row = self.connection.execute(
            """
            select r.repository_id, r.repo_name,
                   (select count(*) from commits c
                    where c.repository_id = r.repository_id) as commits,
                   (select count(*) from languages l
                    where l.repository_id = r.repository_id) as languages
            from repositories r
            where r.repo_name = ? collate nocase
            """,
            (name,),
        ).fetchone()
        if not row:
            print("Repository not found.")
            return

        print(
            f"This will also remove {row['commits']} related commits and "
            f"{row['languages']} language records."
        )
        confirmation = input("Type DELETE to continue: ").strip()
        if confirmation != "DELETE":
            print("Delete canceled.")
            return

        self.connection.execute(
            "delete from repositories where repository_id = ?",
            (row["repository_id"],),
        )
        self.connection.commit()
        print("Repository and its related records were deleted.")

    # ------------------------------ JSON IMPORT ------------------------------

    @staticmethod
    def json_records(archive, member_name, limit):
        """Yield valid JSON objects one line at a time to conserve memory."""
        with archive.open(member_name) as source:
            for line_number, raw_line in enumerate(source, start=1):
                if limit and line_number > limit:
                    break
                try:
                    yield json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

    def repository_id(self, repo_name, cache):
        """Find or create a repository and reuse its ID during an import."""
        key = repo_name.lower()
        if key in cache:
            return cache[key]

        self.connection.execute(
            """
            insert into repositories (repo_name, source)
            values (?, 'GitHub Archive')
            on conflict(repo_name) do nothing
            """,
            (repo_name,),
        )
        row = self.connection.execute(
            "select repository_id from repositories where repo_name = ?",
            (repo_name,),
        ).fetchone()
        cache[key] = row["repository_id"]
        return cache[key]

    def import_archive(self):
        """Import repositories, languages, and commits directly from the ZIP."""
        entered_path = input(f"GitHub Archive ZIP [{DEFAULT_ZIP_FILE}]: ").strip()
        zip_path = Path(entered_path or DEFAULT_ZIP_FILE).expanduser()
        if not zip_path.is_file():
            print("ZIP file not found. Enter its complete path and try again.")
            return

        limit = self.prompt_integer(
            "Maximum records per JSON file (0 imports all records): "
        )
        try:
            with zipfile.ZipFile(zip_path) as archive:
                available = set(archive.namelist())
                required = {
                    "GitHubArchive-Dataset/Sample_Repos.json",
                    "GitHubArchive-Dataset/Languages.json",
                    "GitHubArchive-Dataset/Sample_Commits.json",
                }
                missing = required - available
                if missing:
                    print("The ZIP is missing required GitHub Archive files.")
                    return

                repo_count = self.import_repositories(archive, limit)
                language_count = self.import_languages(archive, limit)
                commit_count = self.import_commits(archive, limit)
                self.connection.commit()
        except (zipfile.BadZipFile, OSError, sqlite3.Error) as error:
            self.connection.rollback()
            print(f"Import failed: {error}")
            return

        print("\nImport complete")
        print(f"Repositories processed: {repo_count:,}")
        print(f"Language records processed: {language_count:,}")
        print(f"Commits processed: {commit_count:,}")

    def import_repositories(self, archive, limit):
        """Upsert repository names and watch counts."""
        member = "GitHubArchive-Dataset/Sample_Repos.json"
        count = 0
        for record in self.json_records(archive, member, limit):
            name = str(record.get("repo_name", "")).strip()
            if not name:
                continue
            try:
                watches = int(record.get("watch_count") or 0)
            except (TypeError, ValueError):
                watches = 0
            self.connection.execute(
                """
                insert into repositories (repo_name, watch_count, source)
                values (?, ?, 'GitHub Archive')
                on conflict(repo_name) do update set
                    watch_count = excluded.watch_count,
                    source = 'GitHub Archive'
                """,
                (name, watches),
            )
            count += 1
            if count % BATCH_SIZE == 0:
                self.connection.commit()
        return count

    def import_languages(self, archive, limit):
        """Normalize each repository's language list into language rows."""
        member = "GitHubArchive-Dataset/Languages.json"
        count = 0
        cache = {}
        for record in self.json_records(archive, member, limit):
            repo_name = str(record.get("repo_name", "")).strip()
            language_list = record.get("language") or []
            if not repo_name or not isinstance(language_list, list):
                continue
            repo_id = self.repository_id(repo_name, cache)
            for language in language_list:
                name = str(language.get("name", "")).strip()
                if not name:
                    continue
                try:
                    byte_count = int(language.get("bytes") or 0)
                except (TypeError, ValueError):
                    byte_count = 0
                self.connection.execute(
                    """
                    insert into languages
                        (repository_id, language_name, byte_count)
                    values (?, ?, ?)
                    on conflict(repository_id, language_name) do update set
                        byte_count = excluded.byte_count
                    """,
                    (repo_id, name, byte_count),
                )
                count += 1
            if count and count % BATCH_SIZE == 0:
                self.connection.commit()
        return count

    def import_commits(self, archive, limit):
        """Store commits and connect them to repositories through a foreign key."""
        member = "GitHubArchive-Dataset/Sample_Commits.json"
        count = 0
        cache = {}
        for record in self.json_records(archive, member, limit):
            commit_hash = str(record.get("commit", "")).strip()
            repo_name = str(record.get("repo_name", "")).strip()
            if not commit_hash or not repo_name:
                continue
            author = record.get("author") or {}
            repo_id = self.repository_id(repo_name, cache)
            self.connection.execute(
                """
                insert into commits
                    (commit_hash, repository_id, author_name, author_email,
                     subject, message)
                values (?, ?, ?, ?, ?, ?)
                on conflict(commit_hash) do update set
                    repository_id = excluded.repository_id,
                    author_name = excluded.author_name,
                    author_email = excluded.author_email,
                    subject = excluded.subject,
                    message = excluded.message
                """,
                (
                    commit_hash,
                    repo_id,
                    str(author.get("name", "Unknown")),
                    str(author.get("email", "")),
                    str(record.get("subject", "")),
                    str(record.get("message", "")),
                ),
            )
            count += 1
            if count % BATCH_SIZE == 0:
                self.connection.commit()
        return count

    # --------------------------- ANALYSIS FEATURES ---------------------------

    def database_dashboard(self):
        """Feature 1: Display a quick dashboard of the SQLite database."""
        row = self.connection.execute(
            """
            select
                (select count(*) from repositories) as repositories,
                (select count(*) from commits) as commits,
                (select count(*) from languages) as language_records,
                (select count(distinct author_name) from commits) as contributors,
                (select coalesce(sum(watch_count), 0) from repositories) as watches
            """
        ).fetchone()
        print("\nSQLite Database Dashboard")
        print(f"Repositories: {row['repositories']:,}")
        print(f"Commits: {row['commits']:,}")
        print(f"Language records: {row['language_records']:,}")
        print(f"Contributors: {row['contributors']:,}")
        print(f"Combined watch count: {row['watches']:,}")

    def top_languages(self):
        """Feature 2: Rank languages by bytes and repository usage."""
        rows = self.connection.execute(
            """
            select language_name, sum(byte_count) as total_bytes,
                   count(distinct repository_id) as repositories
            from languages
            group by language_name
            order by total_bytes desc
            limit 10
            """
        ).fetchall()
        self.print_rows(
            "Top Programming Languages",
            rows,
            lambda row, rank: (
                f"{rank:>2}. {row['language_name']}: {row['total_bytes']:,} bytes "
                f"across {row['repositories']} repositories"
            ),
        )

    def repository_scorecard(self):
        """Feature 3: Combine repository, language, and commit information."""
        name = self.prompt_required("Exact repository name: ")
        row = self.connection.execute(
            """
            select r.repository_id, r.repo_name, r.watch_count,
                   count(distinct c.commit_hash) as commits,
                   count(distinct c.author_name) as contributors,
                   count(distinct l.language_name) as languages,
                   (select coalesce(sum(l2.byte_count), 0)
                    from languages l2
                    where l2.repository_id = r.repository_id) as recorded_bytes
            from repositories r
            left join commits c on c.repository_id = r.repository_id
            left join languages l on l.repository_id = r.repository_id
            where r.repo_name = ? collate nocase
            group by r.repository_id
            """,
            (name,),
        ).fetchone()
        if not row:
            print("Repository not found.")
            return

        primary = self.connection.execute(
            """
            select language_name, byte_count
            from languages
            where repository_id = ?
            order by byte_count desc
            limit 1
            """,
            (row["repository_id"],),
        ).fetchone()
        print(f"\nRepository Scorecard: {row['repo_name']}")
        print(f"Watch count: {row['watch_count']:,}")
        print(f"Imported commits: {row['commits']:,}")
        print(f"Contributors: {row['contributors']:,}")
        print(f"Languages: {row['languages']:,}")
        print(f"Recorded code bytes: {row['recorded_bytes']:,}")
        print(f"Primary language: {primary['language_name'] if primary else 'Unknown'}")

    def contributor_leaderboard(self):
        """Feature 4: Rank contributors using SQL grouping and joins."""
        rows = self.connection.execute(
            """
            select c.author_name, count(*) as commits,
                   count(distinct c.repository_id) as repositories
            from commits c
            group by c.author_name
            order by commits desc, repositories desc, c.author_name
            limit 10
            """
        ).fetchall()
        self.print_rows(
            "Contributor Leaderboard",
            rows,
            lambda row, rank: (
                f"{rank:>2}. {row['author_name']}: {row['commits']} commits "
                f"across {row['repositories']} repositories"
            ),
        )

    def search_commit_messages(self):
        """Feature 5: Search commit subjects and messages with LIKE."""
        keyword = self.prompt_required("Commit keyword: ")
        rows = self.connection.execute(
            """
            select c.commit_hash, c.subject, c.author_name, r.repo_name
            from commits c
            join repositories r on r.repository_id = c.repository_id
            where c.subject like ? collate nocase
               or c.message like ? collate nocase
            order by r.repo_name, c.subject
            limit 20
            """,
            (f"%{keyword}%", f"%{keyword}%"),
        ).fetchall()
        self.print_rows(
            f"Commit Search Results for '{keyword}'",
            rows,
            lambda row, rank: (
                f"{rank:>2}. [{row['commit_hash'][:10]}] {row['repo_name']}\n"
                f"    {row['subject'] or '(No subject)'} — {row['author_name']}"
            ),
        )

    def hidden_gems(self):
        """Feature 6: Find active repositories with lower watch counts."""
        rows = self.connection.execute(
            """
            select r.repo_name, r.watch_count, count(c.commit_hash) as commits,
                   count(distinct c.author_name) as contributors,
                   round(count(c.commit_hash) * 100.0 / (r.watch_count + 1), 2)
                       as activity_score
            from repositories r
            join commits c on c.repository_id = r.repository_id
            group by r.repository_id
            having count(c.commit_hash) >= 2
            order by activity_score desc, commits desc
            limit 10
            """
        ).fetchall()
        self.print_rows(
            "Hidden Gems: High Imported Activity vs. Watch Count",
            rows,
            lambda row, rank: (
                f"{rank:>2}. {row['repo_name']} — score {row['activity_score']}, "
                f"{row['commits']} commits, {row['contributors']} contributors, "
                f"{row['watch_count']} watches"
            ),
        )

    def export_top_repositories(self):
        """Feature 7: Export a joined repository summary to a CSV file."""
        rows = self.connection.execute(
            """
            select r.repo_name, r.watch_count,
                   count(distinct c.commit_hash) as imported_commits,
                   count(distinct l.language_name) as language_count
            from repositories r
            left join commits c on c.repository_id = r.repository_id
            left join languages l on l.repository_id = r.repository_id
            group by r.repository_id
            order by r.watch_count desc
            limit 100
            """
        ).fetchall()
        output_path = Path("sqlite_repository_report.csv")
        with output_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(
                ["repo_name", "watch_count", "imported_commits", "language_count"]
            )
            writer.writerows(tuple(row) for row in rows)
        print(f"Exported {len(rows)} rows to {output_path.resolve()}.")

    @staticmethod
    def print_rows(title, rows, formatter):
        """Display a consistent heading and formatted query results."""
        print(f"\n{title}")
        if not rows:
            print("No matching data is available.")
            return
        for rank, row in enumerate(rows, start=1):
            print(formatter(row, rank))

    def list_repositories(self):
        """Display the first 25 repository names alphabetically."""
        rows = self.connection.execute(
            """
            select repo_name, watch_count
            from repositories
            order by repo_name collate nocase
            limit 25
            """
        ).fetchall()
        self.print_rows(
            "Repository Records",
            rows,
            lambda row, rank: f"{rank:>2}. {row['repo_name']} ({row['watch_count']} watches)",
        )

    @staticmethod
    def display_menu():
        """Show every action available in the terminal program."""
        print("\nGitHub Archive SQLite Menu")
        print("1. Import GitHub Archive JSON")
        print("2. Create a repository")
        print("3. Read a repository")
        print("4. Update a repository")
        print("5. Delete a repository")
        print("6. Show database dashboard")
        print("7. Show top programming languages")
        print("8. Show repository scorecard")
        print("9. Show contributor leaderboard")
        print("10. Search commit messages")
        print("11. Discover hidden-gem repositories")
        print("12. Export repository report to CSV")
        print("13. List repository records")
        print("0. Exit")

    def run(self):
        """Process menu choices until the user selects Exit."""
        actions = {
            "1": self.import_archive,
            "2": self.create_repository,
            "3": self.read_repository,
            "4": self.update_repository,
            "5": self.delete_repository,
            "6": self.database_dashboard,
            "7": self.top_languages,
            "8": self.repository_scorecard,
            "9": self.contributor_leaderboard,
            "10": self.search_commit_messages,
            "11": self.hidden_gems,
            "12": self.export_top_repositories,
            "13": self.list_repositories,
        }
        print(f"Connected to SQLite database: {Path(DATABASE_FILE).resolve()}")
        try:
            while True:
                self.display_menu()
                choice = input("Select an option: ").strip()
                if choice == "0":
                    print("Goodbye!")
                    break
                action = actions.get(choice)
                if not action:
                    print("Invalid option. Enter a number from 0 through 13.")
                    continue
                try:
                    action()
                except sqlite3.Error as error:
                    self.connection.rollback()
                    print(f"SQLite operation failed: {error}")
        finally:
            self.connection.close()


if __name__ == "__main__":
    GitHubSQLiteApp().run()
