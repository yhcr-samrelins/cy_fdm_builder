# from google.cloud import bigquery
import datetime
from dateutil.parser import parse
from FDM_helpers import *
from google.cloud import bigquery
from google.cloud.exceptions import NotFound
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=SyntaxWarning)

# Set global variables
PROJECT = "yhcr-prd-phm-bia-core"
CLIENT = bigquery.Client(project=PROJECT)
DEMOGRAPHICS = f"{PROJECT}.CY_TESTS_MASTER.demographics"
MASTER_PERSON = f"{PROJECT}.CY_TESTS_MASTER.person"

    
class FDMTable:
    
    
    def __init__(self, source_table_full_id, dataset_id):
        self.source_table_full_id = source_table_full_id
        self.dataset_id = dataset_id
        self.person_id_added = False
        
        table_alias = source_table_full_id.split(".")[-1]
        self.table_id = table_alias
        full_table_id = f"{PROJECT}.{self.dataset_id}.{table_alias}"
        self.full_table_id = full_table_id
        
    
    def build(self):
        
        
        if self.person_id_added:
            print(f"#### BUILD PROCESS ALREADY COMPLETED FOR {self.table_id} #####")
        else:
            print(f"\t\t ##### BUILDING TABLE {self.table_id} #####")
            print("_" * 80 + "\n")
            self._copy_table_to_dataset()
            self._clean_identifier_column_names()
            self._insert_person_id_into_table()
            self.person_id_added = True
            print("_" * 80 + "\n")
            print(f"\t ##### BUILD PROCESS FOR {self.table_id} COMPLETE! #####\n")
    
    
    def _get_column_names(self):
        table = CLIENT.get_table(self.full_table_id)
        return [field.name for field in table.schema]
            
            
    def get_identifier_columns(self):
        
        col_names = self._get_column_names()
        
        # find matching identifier columns and correct syntax if required
        identifier_names = ["person_id", "digest", "EDRN"]
        identifier_columns = [identifier for identifier in identifier_names
                              if identifier in col_names]
        return identifier_columns
                                                                                                          
    
    def _copy_table_to_dataset(self):
        # check exists - if so skip
        # if not copy
        
        print(f"1. Copying {self.table_id} to {self.dataset_id}\n")
        try:
            CLIENT.get_table(self.full_table_id)
            print(f"\t* {self.table_id} already exists in {self.dataset_id}.\n\n" 
                  f"\tNOTE: Working from the existing version of {self.table_id}"
                  f"\n\tin {self.dataset_id}. If you wish to begin from scratch with a\n\t" 
                  f"fresh copy, drop the existing table in {self.dataset_id} and run\n\t"
                   ".build() again.\n")
        except NotFound:
            sql = f"""
                CREATE TABLE `{self.full_table_id}` AS
                SELECT * 
                FROM `{self.source_table_full_id}`
            """
            CLIENT.query(sql).result()
            print(f"\t* {self.table_id} copied to {self.dataset_id}!\n")
            
            
    def _clean_identifier_column_names(self):
        
        print(f"2. Checking identifier name syntax:\n")
        
        col_names = self._get_column_names()
        
        # find matching identifier columns and correct syntax if required
        correct_identifiers = ["person_id", "digest", "EDRN"]
        identifier_columns = []
        for identifier in correct_identifiers:
            col_match = [
                col_name for col_name in col_names 
                if col_name.lower() == identifier.lower()
            ] 
            if col_match and col_match[0] not in correct_identifiers:
                rename_columns_in_bigquery(self.full_table_id, 
                                           {col_match[0]: identifier},
                                           verbose=False)
                print(f"\t* {col_match[0]} found - corrected to {identifier}")
            elif col_match:
                print(f"\t* {col_match[0]} found - syntax correct")
        
        if not self.get_identifier_columns():
            raise ValueError(
                "\n\n\tNo identifier columns found! FDM process requires a digest\n\t"
                "or EDRN column in each source table to be able to\n\t"
                "link person_ids.\n\n\t"
                "TIP: If digest/EDRN columns are present under a different name,\n\t"
                "rename the column in question then run .build() again."
            )
                
        
    def _insert_person_id_into_table(self):
        
        print(f"\n3. Adding person_id column:\n")
        
        id_columns = self.get_identifier_columns()
        
        if not id_columns:
            raise ValueError("\tNo identifier column (digest/EDRN) found for adding person_ids!")
            
        if "digest" in id_columns and "EDRN" in id_columns:
            print(f"\tWARNING: both digest and EDRN "
                  + f"found in {self.table_id}. Using digest by default.\n\t"
                  + "This may produce unexpected behaviour!")
            
        if "person_id" in id_columns:
            print(f"\t* {self.table_id} already contains person_id column\n")
            if len(id_columns) > 1:
                and_identifiers = " and ".join(id_columns[1:])
                or_identifiers = " or ".join(id_columns[1:])
                print(f"\tNOTE: {and_identifiers} also "
                      f"found in {self.table_id}. If you\n\twish to rebuild the "
                      f"person_id column from {or_identifiers}, drop the existing\n\t"
                      f"person_id column in {self.table_id} and run .build() again\n")
                
        else:
            sql = f"""
                SELECT b.person_id, a.*
                FROM `{self.full_table_id}` a
                LEFT JOIN `{DEMOGRAPHICS}` b
                ON a.{id_columns[0]} = b.{id_columns[0]}
            """
            job_config = bigquery.QueryJobConfig(
                destination=self.full_table_id,  
                write_disposition="WRITE_TRUNCATE"
            )
            query = CLIENT.query(sql, job_config=job_config)
            query.result()
            print("\t* person_id column added!\n")
            
            
    def _get_event_date_df(self, date_source, yearfirst, dayfirst):

        table = CLIENT.get_table(self.full_table_id)
        col_data = {field.name: field.field_type 
                    for field in table.schema}
        if type(date_source) == list and len(date_source) == 3:
            cast_cols_sql = []
            for col in date_source:
                if col in col_data.keys() and col_data[col] == "STRING":
                    cast_cols_sql.append(col)
                elif col in col_data.keys(): 
                    cast_cols_sql.append(f"CAST({col} AS STRING)")
                else:
                    cast_cols_sql.append(f'"{col}"')
            to_concat_sql = ', "-", '.join(cast_cols_sql) 
            sql = f"""
                SELECT uuid, CONCAT({to_concat_sql}) AS date
                FROM {self.full_table_id}
            """
        else:
            sql = f"""
                SELECT uuid, {date_source} AS date
                FROM {self.full_table_id}
            """

        dates_df = pd.read_gbq(query=sql, project_id=PROJECT)
        
        def date_is_short(date):
            if type(date) is str and len(date) <= 8:
                return True
            else:
                return False
        if all(dates_df.date.apply(date_is_short)):
            print("WARNING: 2 character years are ambiguous e.g. 75 will be parsed\n" 
                  "as 1975 but 70 will be parsed as 2070. Consider converting year.")
            
        def parse_date(x):
            if type(x) is datetime.datetime:
                x = x.date
            return parse(str(x), dayfirst=dayfirst, yearfirst=yearfirst)
        dates_df["parsed_date"] = dates_df.date.apply(parse_date)
        return dates_df[["uuid", "parsed_date"]]


    def add_parsed_date_to_table(self, date_source, date_format="YMD"):
        
        date_format_settings = {
            "YMD": [True, False],
            "DMY": [False, True],
            "MDY": [False, False]
        }

        if "uuid" not in self._get_column_names():
            add_uuid_sql = f"""
                SELECT GENERATE_UUID() AS uuid, *
                FROM {self.full_table_id}
            """
            run_sql_query(add_uuid_sql, destination=self.full_table_id)

        yearfirst, dayfirst = date_format_settings[date_format]
        dates_df = self._get_event_date_df(date_source, 
                                           yearfirst=yearfirst,
                                           dayfirst=dayfirst)
        temp_dates_id = f"{PROJECT}.{self.dataset_id}.tmp_dates"
        dates_df.to_gbq(destination_table=temp_dates_id,
                        project_id=PROJECT,
                        table_schema=[{"name":"parsed_date", "type":"DATE"}],
                        if_exists="replace")

        join_dates_sql = f"""
            SELECT dates.parsed_date, src.*
            FROM `{self.full_table_id}` AS src
            LEFT JOIN {temp_dates_id} as dates
            ON src.uuid = dates.uuid
        """
        run_sql_query(join_dates_sql, destination=self.full_table_id)

        drop_uuid_sql = f"""
            ALTER TABLE {self.full_table_id}
            DROP COLUMN uuid
        """
        run_sql_query(drop_uuid_sql)

        CLIENT.delete_table(temp_dates_id)
        
        
class FDMDataset:
    
    
    def __init__(self, dataset_id, fdm_tables):
        self.dataset_id = dataset_id
        self.person_table_id = f"{PROJECT}.{dataset_id}.person"
        self.tables = fdm_tables
    
    
    def build(self):
        
        print(f"\t\t ##### BUILDING FDM DATASET {self.dataset_id} #####")
        print("_" * 80 + "\n")
        self._check_fdm_tables()
        self._build_person_table()
        self._build_missing_person_ids()
        self._build_person_ids_missing_from_master()
        print("_" * 80 + "\n")
        print(f"\t ##### BUILD PROCESS FOR {self.dataset_id} COMPLETE! #####\n")
        
    
    def _check_fdm_tables(self):
              
        print("1. Checking source input tables:\n")
        
        for table in self.tables:
            
            if not type(table) is FDMTable:
                raise ValueError(
                    f"\t{table} is not an FDM table. All inputs must be built FDM tables."
                    "\n\tCheck and re-initialise FDMDataset with correct input."
                )
            elif not table.person_id_added:
                raise ValueError(
                    f"\n\n\tThe build process for {table.table_id} has not been completed.\n\t" 
                    "All inputs must be built FDM tables. Run the .build() method for the\n\t"
                    f"FDMTable object relating to {table.table_id} then run re-build FDMDataset."
                )
            elif not table.dataset_id == self.dataset_id:
                raise ValueError(
                    f"\t{table.table_id} is not part of the FDM Dataset {self.dataset_id} "
                    f"- it is part of {table.dataset_id}."
                    f"\n\The build process can only be run on tables from the same FDM Dataset."
                )
            else:
                print(f"\t* {table.table_id} - OK")
              
        
    def _build_person_table(self):
        
        print("\n2. Building person table:\n")
        # generate new table with unique person ids
        person_id_union_sql = "\nUNION ALL\n".join(
            [f"SELECT person_id FROM `{table.full_table_id}`"
             for table in self.tables]
        )
        person_ids_sql = f"""
            WITH person_ids AS (
                {person_id_union_sql}
            )
            SELECT DISTINCT person_id
            FROM person_ids
        """
        run_sql_query(person_ids_sql, destination=self.person_table_id) 
        
        # join columns from master person table in query
        print(f"\t* Joining data from master person table")
        full_person_table_sql = f"""
            SELECT a.person_id, b.* EXCEPT(person_id)
            FROM `{self.person_table_id}` a
            INNER JOIN `{MASTER_PERSON}` b
            ON a.person_id = b.person_id
        """
        run_sql_query(full_person_table_sql, 
                      destination=self.person_table_id)
        print("\t* Person table built!\n")
        
    
    def _build_missing_person_ids(self):
        
        print("3. Building table of individuals with no person_id\n")
        select_queries = [
            f"""
                SELECT "{identifier}" AS identifier, {identifier} AS value 
                FROM {table.full_table_id} 
                WHERE person_id IS NULL 
            """ 
            for table in self.tables 
            for identifier in table.get_identifier_columns() 
            if identifier != "person_id"
        ]
        union_query = "\nUNION ALL\n".join(select_queries)
        sql = f"""
            WITH missing_person_ids AS (
                {union_query}
            )
            SELECT *
            FROM missing_person_ids
            GROUP BY identifier, value
        """
        table_id = f"{PROJECT}.{self.dataset_id}.individuals_missing_person_id"
        run_sql_query(sql, destination=table_id) 
        tab = CLIENT.get_table(table_id)
        print(f"\t* individuals_missing_person_id created with {tab.num_rows} entries")
    
            
    def _build_person_ids_missing_from_master(self):
        
        print("4. Building person_ids missing from master table\n")
        # generate new table with unique person ids
        person_id_union_sql = "\nUNION ALL\n".join(
            [f"SELECT person_id FROM `{table.full_table_id}`"
             for table in self.tables]
        )
        missing_ids_sql = f"""
            WITH all_person_ids AS (
                {person_id_union_sql}
            )
            SELECT DISTINCT person_id
            FROM all_person_ids
            WHERE NOT EXISTS(
                SELECT person_id
                FROM `{self.person_table_id}` person_table
                WHERE all_person_ids.person_id = person_table.person_id
            )
            AND person_id IS NOT NULL
        """
        table_id = f"{PROJECT}.{self.dataset_id}.person_ids_missing_from_masater"
        run_sql_query(missing_ids_sql, destination=table_id) 
        
        tab = CLIENT.get_table(table_id)
        print(f"\t* person_ids_missing_from_master created with {tab.num_rows} entries")
        
