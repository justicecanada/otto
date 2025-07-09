import datetime
import os

# settings
from django.conf import settings
from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from librarian.models import Document, Library


class Command(BaseCommand):
    help = (
        "Delete entries in vector store that don't have a corresponding Django object.\n"
        "This should not be needed in practice (if Library delete methods are working correctly), but is a safety measure."
    )

    @signalcommand
    def handle(self, *args, **options):
        # Make a list of database tables that are used by the vector store
        # and that have a corresponding Django model.
        keep_tables = [
            f"data_{uuid_hex}"
            for uuid_hex in Library.objects.values_list("uuid_hex", flat=True)
        ] + ["data_laws_lois__"]

        db = settings.DATABASES["vector_db"]
        connection_string = f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"
        engine = create_engine(connection_string)
        Session = sessionmaker(bind=engine)

        session = Session()
        # Get a list of tables and views in the database
        vector_db_tables = [
            table[0]
            for table in session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
                )
            ).fetchall()
        ]
        vector_db_views = [
            view[0]
            for view in session.execute(
                text(
                    "SELECT table_name FROM information_schema.views WHERE table_schema='public'"
                )
            ).fetchall()
        ]
        print("Tables in vector db:")
        for table in vector_db_tables:
            print(table)
        print("Views in vector db:")
        for view in vector_db_views:
            print(view)
        print("\nTables that should be kept, based on Django:")
        for table in keep_tables:
            print(table)

        # Delete tables/views that are not in the keep_tables list
        delete_tables = set(vector_db_tables) - set(keep_tables)
        delete_views = set(vector_db_views) - set(keep_tables)
        deleted_count = 0
        print("\nTables to delete:")
        for table in delete_tables:
            print(table)
        print("\nViews to delete:")
        for view in delete_views:
            print(view)
        for table in delete_tables:
            session.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
            deleted_count += 1
        for view in delete_views:
            session.execute(text(f"DROP VIEW IF EXISTS {view} CASCADE"))
            deleted_count += 1
        session.commit()
        print(f"\nDeleted {deleted_count} tables/views.")

        # For each Django library, find documents that should be deleted
        print("\nChecking for documents to delete in individual libraries...")
        for library in Library.objects.all():
            library_uuid_hex = library.uuid_hex
            library_documents = Document.objects.filter(
                data_source__in=library.data_sources.all()
            )
            library_document_hexes = set(
                [document.uuid_hex for document in library_documents]
            )
            library_vector_table = f"data_{library_uuid_hex}"
            print(f"\nLibrary: {library}")
            print(f"Library vector table: {library_vector_table}")

            # Get a list of document hexes in the vector store
            # These are found in each row's metadata_ column, property "ref_doc_id"
            vector_store_document_hexes = set(
                [
                    row[0]
                    for row in session.execute(
                        text(
                            f"SELECT metadata_ -> 'ref_doc_id' FROM {library_vector_table}"
                        )
                    ).fetchall()
                ]
            )

            print(f"Documents in vector store: {len(vector_store_document_hexes)}")
            print(f"Documents in Django: {len(library_document_hexes)}")

            # Find documents that are in the vector store but not in Django
            delete_document_hexes = vector_store_document_hexes - library_document_hexes
            print(f"Documents to delete: {len(delete_document_hexes)}")

            # Delete documents that are in the vector store but not in Django
            deleted_count = 0
            for document_hex in delete_document_hexes:
                print(f"Deleting document {document_hex}...")
                stmt = text(
                    f"DELETE FROM {library_vector_table} where "
                    f"(metadata_->>'doc_id')::text = '{document_hex}' "
                )
                session.execute(stmt)
                deleted_count += 1

            session.commit()
            print(f"Deleted {deleted_count} documents.")

        session.close()
