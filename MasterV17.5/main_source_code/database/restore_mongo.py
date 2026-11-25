"""Restore database/dump/*.jsonl exported by the project archive."""
import os
from itertools import islice
from pathlib import Path

from bson import json_util
from dotenv import load_dotenv
from pymongo import MongoClient, ReplaceOne

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / 'backend' / '.env')


def restore():
    client = MongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    dump_dir = ROOT / 'database' / 'dump'
    try:
        for path in sorted(dump_dir.glob('*.jsonl')):
            count = 0
            with path.open(encoding='utf-8') as dump:
                while lines := list(islice(dump, 1000)):
                    docs = [json_util.loads(line) for line in lines if line.strip()]
                    operations = [
                        ReplaceOne({'_id': doc['_id']}, doc, upsert=True)
                        for doc in docs if '_id' in doc
                    ]
                    if operations:
                        db[path.stem].bulk_write(operations, ordered=False)
                    count += len(operations)
            print(f'{path.stem}: {count} documents restored')
        print('Restore complete')
    finally:
        client.close()


if __name__ == '__main__':
    restore()
