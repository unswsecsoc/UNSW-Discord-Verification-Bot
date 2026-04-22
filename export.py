import csv
from typing import Optional
import io
import logging
from pydantic import BaseModel, Field, ValidationError, model_validator, field_validator


class UserSchema(BaseModel):
    discord_id: int
    email: str
    verified: int = Field(default=0, ge=0, le=1)  # must be 0 or 1
    verified_at: Optional[int] = Field(default=None, gt=0, le=2**34)  # nulls ok

    @field_validator('verified_at', mode='before')
    @classmethod
    def empty_str_to_none(cls, value):  
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def validate_both_or_none(self) -> "UserSchema":
        # Must have neither or both of these fields
        if not ((self.verified_at == None) or self.verified):
            raise ValueError("If 'verified_at' is non-empty, verified must be 1.")
        return self


def import_csv_to_db(conn, csv_contents: str) -> tuple[bool, str]:
    validated_rows = []

    try:
        reader = csv.DictReader(io.StringIO(csv_contents))

        for line_num, row in enumerate(reader, start=2):  # start=2 for CSV row numbering
            try:
                user = UserSchema.model_validate(row)
                validated_rows.append(
                    (user.discord_id, user.email, user.verified, user.verified_at)
                )
            except ValidationError as e:
                return False, f"Validation Error on CSV line {line_num}:\n```{e.json(indent=2)}\n```"

        cursor = conn.cursor()
        # Clear existing data
        cursor.execute("DELETE FROM users")

        # Insert new data
        query = """
            INSERT INTO users (discord_id, email, verified, verified_at) 
            VALUES (?, ?, ?, ?)
        """
        cursor.executemany(query, validated_rows)

        conn.commit()
        return True, f"Success: Imported {len(validated_rows)} rows."

    except Exception as e:
        logging.exception(f"An error occurred during a CSV import: ")
        return False, f"An error occurred while importing: {e.__class__.__name__}."


def export_db_to_csv(conn) -> io.StringIO:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    rows = cursor.fetchall()
    columns = [d[0] for d in cursor.description]

    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(columns)
    writer.writerows(rows)
    out.seek(0)
    return out
