"""
Name(s): Jerald Bradley and David Nelson
Date: September 10, 2026
Assignment: GitHub Archive MongoDD CRUD Application
Purpose: Import newline-delimited GitHub Archive JSON, manage repository
records with MongoDB CRUD operations, and provide three data-analysis features.
"""


import json
import os
import re
from collections import Counter
from typing import Dict, Any, List, Optional
from pymongo import MongoClient, errors
from tabulate import tabulate


class GitHubArchiveManager:
    """Handles MongoDB database operations and data analysis across a folder of GitHub Archive files."""

    def __init__(self, folder_path: str = "github_archive", db_name: str = "github_archive", uri: str = "mongodb://localhost:27017/"):
        """Initialize MongoDB client connection and set folder directory context."""
        self.folder_path = folder_path
        try:
            self.client = MongoClient(uri, serverSelectionTimeoutMS=2000)
            self.client.admin.command('ping')
            self.db = self.client[db_name]
            self.commits = self.db["commits"]
            self.contents = self.db["contents"]
            self.languages = self.db["languages"]
            print(f"[+] Successfully connected to MongoDB database '{db_name}'.")
        except errors.ConnectionFailure:
            print("[-] Error: Could not connect to MongoDB. Ensure the service is running.")
            exit(1)

    # ==========================================
    # BULK FOLDER INGESTION
    # ==========================================

    def import_entire_folder(self, folder_path: Optional[str] = None):
        """Scans a folder for all .json files and automatically imports them into MongoDB."""
        target_folder = folder_path or self.folder_path

        if not os.path.exists(target_folder):
            print(f"[-] Error: Folder '{target_folder}' does not exist.")
            return

        json_files = [f for f in os.listdir(target_folder) if f.endswith(".json")]

        if not json_files:
            print(f"[-] No .json files found in folder '{target_folder}'.")
            return

        print(f"\n[*] Found {len(json_files)} JSON file(s) in '{target_folder}'. Starting bulk import...\n")

        for file_name in json_files:
            file_path = os.path.join(target_folder, file_name)
            self._import_single_file(file_path, file_name)

        print("\n[+] Bulk ingestion completed!")

    def _import_single_file(self, file_path: str, file_name: str):
        """Internal helper to ingest a single JSON file into its designated collection."""
        base = file_name.lower()
        if "content" in base:
            collection_name = "contents"
        elif "language" in base:
            collection_name = "languages"
        elif "commit" in base:
            collection_name = "commits"
        else:
            collection_name = os.path.splitext(file_name)[0].lower()

        target_collection = self.db[collection_name]
        inserted_count = 0
        batch = []
        batch_size = 5000

        print(f"[*] Processing '{file_name}' -> collection '{collection_name}'...")

        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line in ("[", "]", ","):
                    continue
                if line.endswith(","):
                    line = line[:-1]

                try:
                    doc = json.loads(line)
                    batch.append(doc)
                except json.JSONDecodeError:
                    continue

                if len(batch) >= batch_size:
                    target_collection.insert_many(batch)
                    inserted_count += len(batch)
                    batch.clear()

            if batch:
                target_collection.insert_many(batch)
                inserted_count += len(batch)

        print(f"    └─ Ingested {inserted_count} documents into '{collection_name}'.")

    # ==========================================
    # CRUD OPERATIONS (COMMITS)
    # ==========================================

    def create_commit(self, commit_hash: str, author_name: str, author_email: str, repo_name: str, message: str) -> bool:
        document = {
            "commit": commit_hash,
            "author": {"name": author_name, "email": author_email},
            "committer": {"name": author_name, "email": author_email},
            "repo_name": [repo_name] if isinstance(repo_name, str) else repo_name,
            "message": message,
            "subject": message.split("\n")[0]
        }
        try:
            res = self.commits.insert_one(document)
            print(f"[+] Commit created with ID: {res.inserted_id}")
            return True
        except Exception as e:
            print(f"[-] Failed to create commit: {e}")
            return False

    def read_commits_by_author(self, author_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        return list(self.commits.find({"author.name": {"$regex": author_name, "$options": "i"}}).limit(limit))

    def update_commit_message(self, commit_hash: str, new_message: str) -> bool:
        res = self.commits.update_one(
            {"commit": commit_hash},
            {"$set": {"message": new_message, "subject": new_message.split("\n")[0]}}
        )
        if res.matched_count > 0:
            print(f"[+] Commit '{commit_hash}' updated successfully.")
            return True
        print(f"[-] No commit found matching hash: {commit_hash}")
        return False

    def delete_commit(self, commit_hash: str) -> bool:
        res = self.commits.delete_one({"commit": commit_hash})
        if res.deleted_count > 0:
            print(f"[+] Commit '{commit_hash}' deleted successfully.")
            return True
        print(f"[-] No commit found matching hash: {commit_hash}")
        return False

    # ==========================================
    # ANALYSIS FEATURES
    # ==========================================

    def feature_repo_name_length_survey(self):
        pipeline = [
            {"$unwind": "$repo_name"},
            {"$group": {"_id": "$repo_name"}},
            {"$project": {"repo_name": "$_id", "length": {"$strLenCP": "$_id"}}},
            {"$sort": {"length": -1}}
        ]
        results = list(self.commits.aggregate(pipeline))
        if not results:
            print("[-] No repository data found in commits collection.")
            return

        print("\n" + "=" * 50)
        print(" FEATURE 1: Repository Name Length Survey")
        print("=" * 50)
        print(f"Longest Name  ({results[0]['length']} chars): {results[0]['repo_name']}")
        print(f"Shortest Name ({results[-1]['length']} chars): {results[-1]['repo_name']}")
        print("=" * 50 + "\n")

    def feature_top_authors_and_repos(self, limit: int = 5):
        author_pipeline = [
            {"$group": {"_id": "$author.name", "commit_count": {"$sum": 1}}},
            {"$sort": {"commit_count": -1}},
            {"$limit": limit}
        ]
        top_authors = list(self.commits.aggregate(author_pipeline))

        print("\n" + "=" * 50)
        print(f" FEATURE 2: Top Contributors & Most Common Languages")
        print("=" * 50)
        print("\n[ Top Commit Authors ]")
        print(tabulate([[a["_id"], a["commit_count"]] for a in top_authors], headers=["Author", "Commits"]))

        if self.languages.count_documents({}, limit=1) > 0:
            lang_pipeline = [
                {"$unwind": "$language"},
                {"$group": {"_id": "$language.name", "bytes_written": {"$sum": "$language.bytes"}}},
                {"$sort": {"bytes_written": -1}},
                {"$limit": limit}
            ]
            top_langs = list(self.languages.aggregate(lang_pipeline))
            print("\n[ Most Used Languages (by bytes) ]")
            print(tabulate([[l["_id"], l["bytes_written"]] for l in top_langs], headers=["Language", "Bytes"]))
        print("=" * 50 + "\n")

    def feature_popular_commit_words(self, top_n: int = 10):
        cursor = self.commits.find({}, {"message": 1})
        stop_words = {"a", "an", "the", "to", "for", "in", "of", "on", "and", "with", "is", "it", "this"}
        word_counts = Counter()

        for doc in cursor:
            words = re.findall(r'\b[a-zA-Z]{2,}\b', doc.get("message", "").lower())
            word_counts.update([w for w in words if w not in stop_words])

        print("\n" + "=" * 50)
        print(f" FEATURE 3: Most Frequent Words in Commit Messages")
        print("=" * 50)
        print(tabulate(word_counts.most_common(top_n), headers=["Word", "Frequency"]))
        print("=" * 50 + "\n")

    def feature_contents_summary(self):
        if self.contents.count_documents({}, limit=1) == 0:
            print("[-] Contents collection is empty. Run bulk import first.")
            return

        pipeline = [
            {"$project": {"size": 1, "ext": {"$arrayElemAt": [{"$split": ["$path", "."]}, -1]}}},
            {"$group": {"_id": "$ext", "count": {"$sum": 1}, "avg_size": {"$avg": "$size"}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        summary = list(self.contents.aggregate(pipeline))
        print("\n" + "=" * 50)
        print(" FEATURE 4: Top File Extensions in Contents")
        print("=" * 50)
        print(tabulate([[s["_id"], s["count"], round(s["avg_size"] or 0, 2)] for s in summary],
                       headers=["Extension", "File Count", "Avg Size (bytes)"]))
        print("=" * 50 + "\n")


# ==========================================
# CLI INTERFACE
# ==========================================

def main():
    folder = "github_archive"
    manager = GitHubArchiveManager(folder_path=folder)

    while True:
        print("\n--- GitHub Archive Auto-Ingest CLI ---")
        print(f"1. Bulk Import All JSON Files from '{folder}'")
        print("2. Create New Commit (C)")
        print("3. Read Commits by Author (R)")
        print("4. Update Commit Message (U)")
        print("5. Delete Commit (D)")
        print("6. Feature 1: Repo Name Length Survey")
        print("7. Feature 2: Top Contributors & Languages")
        print("8. Feature 3: Commit Message Word Frequency")
        print("9. Feature 4: Contents File Type Analysis")
        print("0. Exit")

        choice = input("\nEnter choice [0-9]: ").strip()

        if choice == "1":
            custom_folder = input(f"Enter folder path [Press Enter for default '{folder}']: ").strip()
            manager.import_entire_folder(custom_folder if custom_folder else None)
        elif choice == "2":
            manager.create_commit(
                input("SHA: "), input("Author Name: "), input("Author Email: "), input("Repo: "), input("Message: ")
            )
        elif choice == "3":
            results = manager.read_commits_by_author(input("Author search term: "))
            rows = [[d.get('commit', '')[:8], d.get('author', {}).get('name', ''), d.get('subject', '')[:40]] for d in results]
            print(tabulate(rows, headers=["SHA", "Author", "Subject"]))
        elif choice == "4":
            manager.update_commit_message(input("Commit SHA: "), input("New Message: "))
        elif choice == "5":
            manager.delete_commit(input("Commit SHA: "))
        elif choice == "6":
            manager.feature_repo_name_length_survey()
        elif choice == "7":
            manager.feature_top_authors_and_repos()
        elif choice == "8":
            manager.feature_popular_commit_words()
        elif choice == "9":
            manager.feature_contents_summary()
        elif choice == "0":
            print("Exiting application.")
            break


if __name__ == "__main__":
    main()
