"""
Name: Jerald Bradley
Date: September 3, 2026
Assignment: GitHub Archive Redis CRUD Application
Purpose: Import newline-delimited GitHub Archive JSON, manage repository
records with Redis CRUD operations, and provide three data-analysis features.
"""

import json
import os
import zipfile
from pathlib import Path

import redis


class GitHubRedisApp:
    """Terminal application for managing GitHub data stored in Redis."""

    def __init__(self):
        self.database = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            decode_responses=True,
        )

    @staticmethod
    def prompt_required(message):
        """Return a nonblank value entered by the user."""
        while True:
            value = input(message).strip()
            if value:
                return value
            print("A value is required.")

    @staticmethod
    def prompt_integer(message, minimum=0, allow_blank=False):
        """Validate numeric input and optionally permit a blank response."""
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

    @staticmethod
    def redis_key(repository_name):
        """Create a predictable Redis key for a repository record."""
        return f"repo:{repository_name.lower()}"

    # CREATE: Store a new repository as a Redis hash.
    def create_repository(self):
        name = self.prompt_required("Repository name (owner/repository): ")
        key = self.redis_key(name)
        if self.database.exists(key):
            print("That repository already exists. Use Update instead.")
            return

        record = {
            "repo_name": name,
            "description": input("Description: ").strip(),
            "primary_language": input("Primary language: ").strip(),
            "watch_count": str(self.prompt_integer("Watch count: ")),
            "source": "user",
        }
        with self.database.pipeline() as pipe:
            pipe.hset(key, mapping=record)
            pipe.sadd("repositories", name.lower())
            pipe.zadd("repositories:watchers", {name: int(record["watch_count"])})
            pipe.execute()
        print(f"Created '{name}'.")

    # READ: Retrieve and display one repository hash.
    def read_repository(self):
        name = self.prompt_required("Repository name: ")
        record = self.database.hgetall(self.redis_key(name))
        if not record:
            print("Repository not found.")
            return
        print("\nRepository record")
        for field, value in sorted(record.items()):
            print(f"{field.replace('_', ' ').title()}: {value}")

    # UPDATE: Change selected fields without replacing the full record.
    def update_repository(self):
        name = self.prompt_required("Repository name to update: ")
        key = self.redis_key(name)
        current = self.database.hgetall(key)
        if not current:
            print("Repository not found.")
            return

        print("Press Enter to keep the current value.")
        description = input(f"Description [{current.get('description', '')}]: ").strip()
        language = input(
            f"Primary language [{current.get('primary_language', '')}]: "
        ).strip()
        watches = self.prompt_integer(
            f"Watch count [{current.get('watch_count', '0')}]: ", allow_blank=True
        )

        changes = {}
        if description:
            changes["description"] = description
        if language:
            changes["primary_language"] = language
        if watches is not None:
            changes["watch_count"] = str(watches)

        if not changes:
            print("No changes were entered.")
            return

        with self.database.pipeline() as pipe:
            pipe.hset(key, mapping=changes)
            if watches is not None:
                pipe.zadd("repositories:watchers", {current["repo_name"]: watches})
            pipe.execute()
        print(f"Updated '{current['repo_name']}'.")

    # DELETE: Remove a repository and its supporting index entries.
    def delete_repository(self):
        name = self.prompt_required("Repository name to delete: ")
        key = self.redis_key(name)
        current = self.database.hgetall(key)
        if not current:
            print("Repository not found.")
            return
        confirm = input(f"Type YES to delete '{current['repo_name']}': ").strip()
        if confirm != "YES":
            print("Delete canceled.")
            return
        with self.database.pipeline() as pipe:
            pipe.delete(key)
            pipe.srem("repositories", name.lower())
            pipe.zrem("repositories:watchers", current["repo_name"])
            pipe.execute()
        print("Repository deleted.")

    def import_archive(self):
        """Stream selected JSON Lines files directly from the supplied ZIP."""
        zip_path = Path(self.prompt_required("Path to the GitHub Archive ZIP: ")).expanduser()
        if not zip_path.is_file():
            print("ZIP file not found.")
            return
        limit = self.prompt_integer(
            "Maximum records per file (0 imports every record): ", minimum=0
        )
        try:
            with zipfile.ZipFile(zip_path) as archive:
                imported = {
                    "repositories": self._import_repositories(archive, limit),
                    "languages": self._import_languages(archive, limit),
                    "commits": self._import_commits(archive, limit),
                }
        except (zipfile.BadZipFile, KeyError, OSError) as error:
            print(f"Import failed: {error}")
            return
        print("\nImport complete")
        for category, count in imported.items():
            print(f"{category.title()}: {count:,}")

    @staticmethod
    def _records(archive, member_name, limit):
        """Yield valid JSON objects one line at a time to conserve memory."""
        with archive.open(member_name) as binary_file:
            for index, raw_line in enumerate(binary_file, start=1):
                if limit and index > limit:
                    break
                try:
                    yield json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

    def _import_repositories(self, archive, limit):
        member = "GitHubArchive-Dataset/Sample_Repos.json"
        count = 0
        with self.database.pipeline(transaction=False) as pipe:
            for record in self._records(archive, member, limit):
                name = record.get("repo_name")
                if not name:
                    continue
                watches = int(record.get("watch_count") or 0)
                pipe.hset(
                    self.redis_key(name),
                    mapping={
                        "repo_name": name,
                        "watch_count": watches,
                        "description": "",
                        "primary_language": "",
                        "source": "GitHub Archive",
                    },
                )
                pipe.sadd("repositories", name.lower())
                pipe.zadd("repositories:watchers", {name: watches})
                count += 1
                if count % 1000 == 0:
                    pipe.execute()
            pipe.execute()
        return count

    def _import_languages(self, archive, limit):
        member = "GitHubArchive-Dataset/Languages.json"
        count = 0
        with self.database.pipeline(transaction=False) as pipe:
            for record in self._records(archive, member, limit):
                repo_name = record.get("repo_name", "")
                languages = record.get("language") or []
                if not repo_name or not languages:
                    continue
                primary = max(languages, key=lambda item: int(item.get("bytes") or 0))
                pipe.hset(self.redis_key(repo_name), mapping={
                    "repo_name": repo_name,
                    "primary_language": primary.get("name", "Unknown"),
                    "source": "GitHub Archive",
                })
                pipe.sadd("repositories", repo_name.lower())
                for language in languages:
                    name = language.get("name")
                    byte_count = int(language.get("bytes") or 0)
                    if name:
                        pipe.zincrby("languages:bytes", byte_count, name)
                count += 1
                if count % 1000 == 0:
                    pipe.execute()
            pipe.execute()
        return count

    def _import_commits(self, archive, limit):
        member = "GitHubArchive-Dataset/Sample_Commits.json"
        count = 0
        with self.database.pipeline(transaction=False) as pipe:
            for record in self._records(archive, member, limit):
                commit_id = record.get("commit")
                repo_name = record.get("repo_name")
                author = (record.get("author") or {}).get("name", "Unknown")
                if not commit_id or not repo_name:
                    continue
                pipe.hset(f"commit:{commit_id}", mapping={
                    "commit": commit_id,
                    "repo_name": repo_name,
                    "author": author,
                    "subject": record.get("subject", ""),
                })
                pipe.lpush(f"contributor:{author.lower()}:commits", commit_id)
                pipe.ltrim(f"contributor:{author.lower()}:commits", 0, 99)
                pipe.zincrby("authors:commits", 1, author)
                pipe.zincrby("repositories:commits", 1, repo_name)
                count += 1
                if count % 1000 == 0:
                    pipe.execute()
            pipe.execute()
        return count

    # FEATURE 1: Analyze programming-language popularity by total bytes.
    def show_top_languages(self):
        results = self.database.zrevrange(
            "languages:bytes", 0, 9, withscores=True
        )
        self._show_ranking("Top Programming Languages by Bytes", results)

    # FEATURE 2: Rank repositories by GitHub watch count.
    def show_top_watched(self):
        results = self.database.zrevrange(
            "repositories:watchers", 0, 9, withscores=True
        )
        self._show_ranking("Top Watched Repositories", results)

    @staticmethod
    def _show_ranking(title, results):
        print(f"\n{title}")
        if not results:
            print("No imported data is available.")
            return
        for rank, (name, score) in enumerate(results, start=1):
            print(f"{rank:>2}. {name}: {int(score):,}")

    # FEATURE 3: Present a contributor's most recently imported commits.
    def show_contributor_history(self):
        author = self.prompt_required("Contributor name: ")
        ids = self.database.lrange(f"contributor:{author.lower()}:commits", 0, 9)
        if not ids:
            print("No commits were found for that contributor.")
            return
        print(f"\nRecent imported commits for {author}")
        for commit_id in ids:
            commit = self.database.hgetall(f"commit:{commit_id}")
            print(f"- {commit_id[:10]} | {commit.get('repo_name', '')}")
            print(f"  {commit.get('subject', '(No subject)')}")

    # FEATURE 4: Pull author names from imported commit JSON and rank them.
    def show_author_names(self):
        results = self.database.zrevrange(
            "authors:commits", 0, 24, withscores=True
        )
        print("\nAuthors Pulled from Sample_Commits.json")
        if not results:
            print("No author data is available. Import commit data first.")
            return
        for rank, (author, commit_count) in enumerate(results, start=1):
            print(f"{rank:>2}. {author} ({int(commit_count):,} imported commit(s))")

    def list_repositories(self):
        """Show a small alphabetical sample of indexed repositories."""
        names = sorted(self.database.smembers("repositories"))[:25]
        if not names:
            print("No repository records are available.")
            return
        print("\nRepository records (first 25 alphabetically)")
        for name in names:
            print(f"- {name}")

    @staticmethod
    def display_menu():
        print("\nGitHub Archive Redis Menu")
        print("1. Import GitHub Archive JSON")
        print("2. Create a repository")
        print("3. Read a repository")
        print("4. Update a repository")
        print("5. Delete a repository")
        print("6. Show top programming languages")
        print("7. Show top watched repositories")
        print("8. Show contributor commit history")
        print("9. Show author names from commit JSON")
        print("10. List repository records")
        print("0. Exit")

    def run(self):
        """Connect to Redis and process menu selections until Exit."""
        try:
            self.database.ping()
        except redis.exceptions.RedisError as error:
            print(f"Unable to connect to Redis: {error}")
            print("Start Redis and verify REDIS_HOST, REDIS_PORT, and REDIS_DB.")
            return

        actions = {
            "1": self.import_archive,
            "2": self.create_repository,
            "3": self.read_repository,
            "4": self.update_repository,
            "5": self.delete_repository,
            "6": self.show_top_languages,
            "7": self.show_top_watched,
            "8": self.show_contributor_history,
            "9": self.show_author_names,
            "10": self.list_repositories,
        }
        print("Connected to Redis successfully.")
        while True:
            self.display_menu()
            choice = input("Select an option: ").strip()
            if choice == "0":
                print("Goodbye!")
                break
            action = actions.get(choice)
            if action:
                try:
                    action()
                except redis.exceptions.RedisError as error:
                    print(f"Redis operation failed: {error}")
            else:
                print("Invalid option. Enter a number from 0 through 10.")


if __name__ == "__main__":
    GitHubRedisApp().run()
