# Generated by Django 5.1.5 on 2025-02-12 15:27

"""
Tries to alter the "metadata_" column from JSON to JSONB in PostgreSQL
for each table in the vector_db database.
"""

from django.db import migrations
from django.conf import settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text


def alter_metadata_jsonb(apps, schema_editor):

    sql_string = """
        DO $$ DECLARE
            r RECORD;
        BEGIN
            FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                EXECUTE 'ALTER TABLE ' || r.tablename || ' ALTER COLUMN metadata_ SET DATA TYPE JSONB USING metadata_::JSONB';
            END LOOP;
        END $$;
        """

    db = settings.DATABASES["vector_db"]
    connection_string = f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    engine = create_engine(connection_string)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.execute(text(sql_string))
    session.commit()
    session.close()


class Migration(migrations.Migration):

    dependencies = [
        ("librarian", "0015_alter_document_extracted_title_and_more"),
    ]

    operations = [
        migrations.RunPython(alter_metadata_jsonb),
    ]
