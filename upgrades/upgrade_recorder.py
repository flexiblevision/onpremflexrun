from pymongo import MongoClient
import datetime
import string
import sys
import getopt


client          = MongoClient("172.17.0.1")
upgrade_records = client["fvonprem"]["upgrade_records"]

def initialize(id, num_steps):
    if not id:
        print('Must pass an id')
        return

    record = {
        "cur_step_txt": "Initializing",
        "upgrade_steps": num_steps,
        "cur_step": 0, 
        "last_updated": str(datetime.datetime.now()),
        "start_time": str(datetime.datetime.now()),
        "end_time": None,
        "state": "running",
        "log": "",
        "id": id 
    }
    updated_record = upgrade_records.update_one({'id': id}, {'$set': record}, True)
    return record

def get_record(id):
    record = upgrade_records.find_one({'id': id})
    return record

def update(record, cur_step, text):
    record['log'] = record['log'] + " # " + text
    record['last_updated'] = str(datetime.datetime.now())
    record['cur_step'] = cur_step
    record['cur_step_txt'] = text
    if cur_step == record['upgrade_steps']:
        record['end_time'] = str(datetime.datetime.now())
        record['state']    = "completed"

    if '_id' in record: del record['_id']
    print(record, '<<<<<<<<<')
    updated_record = upgrade_records.update_one({'id': record['id']}, {'$set': record}, True)


def main(argv):
    print(argv)
    try:
        opts, _ = getopt.getopt(argv, "i:t:s:c:")
    except getopt.GetoptError:
        sys.exit(2)

    id        = None
    text      = None
    num_steps = None
    cur_step  = None
    for opt, arg in opts:
        if opt == '-i':
            id = arg

        elif opt == '-t':
            text = arg

        elif opt == '-s':
            num_steps = int(arg)
        
        elif opt == '-c':
            cur_step = int(arg)

    if id:
        record = get_record(id)
        if not record:
            record = initialize(id, num_steps)
        else:
            update(record, cur_step, text)


if __name__ == "__main__":
    main(sys.argv[1:])
