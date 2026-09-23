This folder contains:
cloning_git_repo_locally_and_repo_file_parser.py: This python script clones the target github repository locally, parsers and chunks each file and classifies 
them into Tier 1, 2, 3 based on the file type and file contents. It produces a folder locally that contains the intermediate parsed json files ready to be
ingested to neo4j aura dB.
code_and_git_ingestion_to_neo4j.py: This python script ingests the data into neo4j by creating nodes containing both content and its embeddings, and creating 
relationships using Cypher Query Language.
