"""
Name: Jerald Bradley
Date: September 23, 2026
Assignment: Python CRUD Application Using Neo4j
Purpose: Import Amazon review data and provide a simple menu for creating,
         reading, and deleting Neo4j nodes and relationships.
"""

import json
from pathlib import Path

import tkinter as tk
from tkinter import simpledialog

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable



# Neo4j connection settings for a local database.
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USERNAME = "neo4j"
NEO4J_DATABASE = "neo4j"


def required_input(message):
    """Prompt until the user enters a value."""
    while True:
        value = input(message).strip()
        if value:
            return value
        print("A value is required.")


def load_json_file(file_name):
    """Read the supplied JSON Lines file and return its records."""
    path = Path(file_name.strip().strip('"'))
    records = []

    with path.open("r", encoding="utf-8") as json_file:
        for line in json_file:
            if line.strip():
                records.append(json.loads(line))

    return records


def create_constraints(driver):
    """Create uniqueness constraints to prevent duplicate nodes."""
    queries = [
        "create constraint category_name if not exists "
        "for (n:Category) require n.name is unique",
        "create constraint product_name if not exists "
        "for (n:Product) require n.name is unique",
        "create constraint review_id if not exists "
        "for (n:Review) require n.review_id is unique",
        "create constraint reviewer_name if not exists "
        "for (n:Reviewer) require n.name is unique",
    ]

    with driver.session(database=NEO4J_DATABASE) as session:
        for query in queries:
            session.run(query).consume()


def run_batch_query(driver, query, records):
    """Run one Cypher query against the complete list of records."""
    with driver.session(database=NEO4J_DATABASE) as session:
        session.run(query, records=records).consume()


def import_data(driver, records):
    """Create the required nodes and relationships from the JSON records."""
    print("Creating Category nodes...")
    run_batch_query(
        driver,
        """
        unwind $records as row
        merge (:Category {name: row.product_category})
        """,
        records,
    )

    print("Creating Product nodes...")
    run_batch_query(
        driver,
        """
        unwind $records as row
        merge (:Product {name: row.product_id})
        """,
        records,
    )

    print("Creating Review nodes...")
    run_batch_query(
        driver,
        """
        unwind $records as row
        merge (review:Review {review_id: row.review_id})
        set review.title = row.review_title,
            review.content = row.review_body,
            review.stars = toInteger(row.stars)
        """,
        records,
    )

    print("Creating Reviewer nodes...")
    run_batch_query(
        driver,
        """
        unwind $records as row
        merge (:Reviewer {name: row.reviewer_id})
        """,
        records,
    )

    print("Connecting Products to Categories...")
    run_batch_query(
        driver,
        """
        unwind $records as row
        match (product:Product {name: row.product_id})
        match (category:Category {name: row.product_category})
        merge (product)-[:IN_CATEGORY]->(category)
        """,
        records,
    )

    print("Connecting Reviews to Reviewers...")
    run_batch_query(
        driver,
        """
        unwind $records as row
        match (review:Review {review_id: row.review_id})
        match (reviewer:Reviewer {name: row.reviewer_id})
        merge (reviewer)-[:WROTE]->(review)
        """,
        records,
    )

    print("Connecting Reviews to Products...")
    run_batch_query(
        driver,
        """
        unwind $records as row
        match (review:Review {review_id: row.review_id})
        match (product:Product {name: row.product_id})
        merge (product)-[:HAS_REVIEW]->(review)
        """,
        records,
    )


# Menu option 1: Create a Category, Product, Review, or Reviewer node.
def create_node(driver):
    print("\n1. Category")
    print("2. Product")
    print("3. Review")
    print("4. Reviewer")
    choice = input("Select a node label: ").strip()

    if choice == "1":
        name = required_input("Category name: ")
        query = "merge (:Category {name: $name})"
        values = {"name": name}
    elif choice == "2":
        name = required_input("Product name: ")
        query = "merge (:Product {name: $name})"
        values = {"name": name}
    elif choice == "3":
        review_id = required_input("Review ID: ")
        title = required_input("Review title: ")
        content = required_input("Review content: ")

        try:
            stars = int(input("Stars (1-5): "))
            if stars < 1 or stars > 5:
                raise ValueError
        except ValueError:
            print("Stars must be a whole number from 1 through 5.")
            return

        query = """
        merge (review:Review {review_id: $review_id})
        set review.title = $title,
            review.content = $content,
            review.stars = $stars
        """
        values = {
            "review_id": review_id,
            "title": title,
            "content": content,
            "stars": stars,
        }
    elif choice == "4":
        name = required_input("Reviewer name/reviewer_id: ")
        query = "merge (:Reviewer {name: $name})"
        values = {"name": name}
    else:
        print("Invalid selection.")
        return

    with driver.session(database=NEO4J_DATABASE) as session:
        session.run(query, **values).consume()
    print("Node created successfully.")


# Menu option 2: Create Product-Category or Product-Review relationships.
def create_relationship(driver):
    print("\n1. Product to Category")
    print("2. Product to Review")
    choice = input("Select a relationship: ").strip()
    product_name = required_input("Product name: ")

    if choice == "1":
        category_name = required_input("Category name: ")
        query = """
        match (product:Product {name: $product_name})
        match (category:Category {name: $target})
        merge (product)-[:IN_CATEGORY]->(category)
        return product
        """
        target = category_name
    elif choice == "2":
        review_id = required_input("Review ID: ")
        query = """
        match (product:Product {name: $product_name})
        match (review:Review {review_id: $target})
        merge (product)-[:HAS_REVIEW]->(review)
        return product
        """
        target = review_id
    else:
        print("Invalid selection.")
        return

    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(
            query, product_name=product_name, target=target
        ).single()

    if result:
        print("Relationship created successfully.")
    else:
        print("Relationship not created. Check that both nodes exist.")


# Menu option 3: Count Products related to a Category.
def count_products(driver):
    category_name = required_input("Category name: ")
    query = """
    match (product:Product)-[:IN_CATEGORY]->
          (category:Category {name: $category_name})
    return count(product) as total
    """

    with driver.session(database=NEO4J_DATABASE) as session:
        total = session.run(
            query, category_name=category_name
        ).single()["total"]
    print(f"Products in {category_name}: {total}")


# Menu option 4: Count Reviews related to a Reviewer.
def count_reviews(driver):
    reviewer_name = required_input("Reviewer name/reviewer_id: ")
    query = """
    match (reviewer:Reviewer {name: $reviewer_name})-[:WROTE]->
          (review:Review)
    return count(review) as total
    """

    with driver.session(database=NEO4J_DATABASE) as session:
        total = session.run(
            query, reviewer_name=reviewer_name
        ).single()["total"]
    print(f"Reviews by {reviewer_name}: {total}")


# Menu option 5: Delete one Category and its relationships.
def delete_category(driver):
    category_name = required_input("Category name to delete: ")
    query = """
    match (category:Category {name: $category_name})
    detach delete category
    """

    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(query, category_name=category_name).consume()
    print(f"Deleted {result.counters.nodes_deleted} Category node(s).")


# Menu option 6: Delete every relationship but keep the nodes.
def delete_all_relationships(driver):
    confirm = input("Type YES to delete all relationships: ").strip()
    if confirm != "YES":
        print("Delete canceled.")
        return

    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run("match ()-[relationship]->() delete relationship").consume()
    print(f"Deleted {result.counters.relationships_deleted} relationship(s).")


# Menu option 7: Delete every node and remaining relationship.
def delete_all_nodes(driver):
    confirm = input("Type YES to delete all nodes: ").strip()
    if confirm != "YES":
        print("Delete canceled.")
        return

    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run("match (node) detach delete node").consume()
    print(f"Deleted {result.counters.nodes_deleted} node(s).")


def display_menu():
    """Display the simplified menu shown in the assignment example."""
    print("\nType in a number and press Enter to execute the menu option.")
    print("1. Create a new node")
    print("2. Create a new relationship")
    print("3. Count Products per Category")
    print("4. Count Reviews per Reviewer")
    print("5. Delete a category")
    print("6. Delete all relationships")
    print("7. Delete all nodes")
    print("8. Exit the program")


def main():
    """Connect, import the file, and run the menu."""
    root = tk.Tk()
    root.withdraw()
    password = simpledialog.askstring(
        "Neo4j Login",
        "Enter your Neo4j password:",
        show="*"
    )
    # Close the hidden Tkinte window after receiving the password
    root.destroy()

    # Stop the program if the user cancels or leaves the password blank
    if not password:
        print("A password is required.")
        return

    # Ask the user for the location of the JSON dataset
    file_name = required_input("Enter the path to the JSON file: ")

    # Create the Neo4j driver using the database URI,
    # username, and password entered by the user
    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USERNAME, password),
    )

    try:
        print("\nConnecting to local Neo4j database...")
        driver.verify_connectivity()
        create_constraints(driver)

        print("Importing data from file...")
        records = load_json_file(file_name)
        import_data(driver, records)

        while True:
            display_menu()
            choice = input("Enter your choice: ").strip()

            if choice == "1":
                create_node(driver)
            elif choice == "2":
                create_relationship(driver)
            elif choice == "3":
                count_products(driver)
            elif choice == "4":
                count_reviews(driver)
            elif choice == "5":
                delete_category(driver)
            elif choice == "6":
                delete_all_relationships(driver)
            elif choice == "7":
                delete_all_nodes(driver)
            elif choice == "8":
                print("Program closed.")
                break
            else:
                print("Please enter a number from 1 through 8.")

    except FileNotFoundError:
        print("The JSON file was not found. Check the path and try again.")
    except json.JSONDecodeError as error:
        print(f"Invalid JSON data near line {error.lineno}.")
    except AuthError:
        print("Authentication failed. Check your Neo4j password.")
    except ServiceUnavailable:
        print("Neo4j is unavailable. Make sure the database is running.")
    except Neo4jError as error:
        print(f"Neo4j error: {error}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
