# -*- coding: utf-8 -*-
"""
Imports user weights from a CSV into a MongoDB.

Matthew Alger
Research School of Astronomy and Astrophysics
The Australian National University
2017
"""

import csv
import pymongo

MONGO_HOST = 'localhost'
MONGO_PORT = 27017
DATABASE = 'radio'
COLLECTION = 'user_weights_dr1'
CSV_PATH = '/mimsy/alger/dr1/rgz/user_weights_dr1.csv'

client = pymongo.MongoClient(MONGO_HOST, MONGO_PORT)
user_weights_db = client[DATABASE][COLLECTION]

with open(CSV_PATH) as csv_file:
  reader = csv.DictReader(csv_file)

  for row in reader:
    user_weights_db.insert({
      'user_name': row['user_name'],
      'agreed': row['agreed'],
      'gs_seen': row['gs_seen'],
      'weight': row['weight'],
    })
