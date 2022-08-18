from pymongo import MongoClient
import datetime
import string
import sys
import getopt

client          = MongoClient("172.17.0.1")
upgrade_records = client["fvonprem"]["upgrade_records"]

def ms_timestamp():
    return int(datetime.datetime.now().timestamp()*1000)

def initialize(id, num_steps):
    if not id:
        print('Must pass an id')
        return

    record = {
        "cur_step_txt": "Initializing",
        "upgrade_steps": num_steps,
        "cur_step": 0, 
        "last_updated": ms_timestamp(),
        "start_time": ms_timestamp(),
        "end_time": None,
        "state": "running",
        "log": "",
        "id": id 
    }

    # update any running upgrade records to failed
    upgrade_records.update_one({'state': 'running'}, {'$set': {'state': 'failed'}})
    updated_record = upgrade_records.update_one({'id': id}, {'$set': record}, True)
    return record

def get_record(id):
    record = upgrade_records.find_one({'id': id})
    return record

def update(record, cur_step, text):
    if 'log' not in record: record['log'] = ''
    record['log'] = record['log'] + " # " + text
    record['last_updated'] = ms_timestamp()
    record['cur_step'] = cur_step
    record['cur_step_txt'] = text
    if int(cur_step) == int(record['upgrade_steps']):
        record['end_time'] = ms_timestamp()
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
