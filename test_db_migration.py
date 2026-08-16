#!/usr/bin/env python3
"""Test database migration and fix payment_method schema."""

import sqlite3
import json
from pathlib import Path
from datetime import date

DB_PATH = Path("data/finbot.db")

def check_schema():
    """Check current database schema."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("\n" + "="*60)
    print("DATABASE SCHEMA CHECK")
    print("="*60)
    
    # Check transactions table
    cur.execute("PRAGMA table_info(transactions)")
    cols = cur.fetchall()
    print("\ntransactions columns:")
    for col in cols:
        print(f"  - {col[1]:20} {col[2]}")
    
    # Check recurring_charges table
    cur.execute("PRAGMA table_info(recurring_charges)")
    cols = cur.fetchall()
    print("\nrecurring_charges columns:")
    for col in cols:
        print(f"  - {col[1]:20} {col[2]}")
    
    conn.close()

def show_recent_transactions():
    """Show recent transactions."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    print("\n" + "="*60)
    print("RECENT TRANSACTIONS")
    print("="*60)
    
    # Get recent transactions
    cur.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 10")
    rows = cur.fetchall()
    
    if not rows:
        print("(No transactions)")
    else:
        for row in rows:
            print(f"\nID {row['id']}:")
            print(f"  type: {row['type']}")
            print(f"  amount: {row['amount']}")
            print(f"  category: {row['category']}")
            print(f"  description: {row['description']}")
            print(f"  txn_date: {row['txn_date']}")
            # Try to read payment_method if it exists
            try:
                pm = row['payment_method']
                print(f"  payment_method: {pm}")
            except (IndexError, ValueError):
                print(f"  payment_method: (column doesn't exist)")
    
    conn.close()

def apply_migration():
    """Apply migration to add payment_method column."""
    print("\n" + "="*60)
    print("APPLYING MIGRATION")
    print("="*60)
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Check if payment_method exists in transactions
        cur.execute("PRAGMA table_info(transactions)")
        cols = {row[1] for row in cur.fetchall()}
        if "payment_method" not in cols:
            cur.execute("ALTER TABLE transactions ADD COLUMN payment_method TEXT")
            print("✓ Added payment_method column to transactions")
        else:
            print("✓ payment_method column already exists in transactions")
        
        # Check if payment_method exists in recurring_charges
        cur.execute("PRAGMA table_info(recurring_charges)")
        cols = {row[1] for row in cur.fetchall()}
        if "payment_method" not in cols:
            cur.execute("ALTER TABLE recurring_charges ADD COLUMN payment_method TEXT")
            print("✓ Added payment_method column to recurring_charges")
        else:
            print("✓ payment_method column already exists in recurring_charges")
        
        conn.commit()
        print("\n✓ Migration completed successfully")
    except Exception as e:
        print(f"✗ Migration error: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"Database does not exist at {DB_PATH}")
        print("It will be created on first bot run.")
    else:
        print(f"Database found at {DB_PATH}")
        check_schema()
        show_recent_transactions()
        apply_migration()
        check_schema()  # Show schema after migration
