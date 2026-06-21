import sqlite3
import pymysql

sqlite_conn = sqlite3.connect("staffing.db")
sqlite_conn.row_factory = sqlite3.Row

mysql_conn = pymysql.connect(
    host="172.26.13.159",
    user="staffapp",
    password="Password123!",
    database="staffing",
    charset="utf8mb4"
)

tables = [
    "users",
    "companies",
    "company_contacts",
    "company_locations",
    "institutions",
    "job_profiles",
    "job_postings",
    "recruitment_sources",
    "candidates",
    "candidate_access_logs",
    "candidate_custom_question_responses",
    "candidate_interview_responses",
    "email_configs",
    "email_rules",
    "interview_questions",
    "interview_rounds",
    "report_definitions",
    "report_shares",
    "app_configs"
]

for table in tables:
    print(f"Migrating {table}")

    cur_sqlite = sqlite_conn.cursor()
    cur_sqlite.execute(f"SELECT * FROM {table}")

    rows = cur_sqlite.fetchall()

    if not rows:
        print(f"  No rows")
        continue

    columns = rows[0].keys()

    placeholders = ",".join(["%s"] * len(columns))
    column_list = ",".join(columns)

    cur_mysql = mysql_conn.cursor()

    for row in rows:
        values = [row[col] for col in columns]

        sql = f"""
        INSERT INTO {table}
        ({column_list})
        VALUES ({placeholders})
        """

        try:
            cur_mysql.execute(sql, values)
        except Exception as e:
            print(f"Failed row in {table}: {e}")

    mysql_conn.commit()
    print(f"  Migrated {len(rows)} rows")

mysql_conn.close()
sqlite_conn.close()

print("Migration completed")
