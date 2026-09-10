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
        "components": [],
        "id": id 
    }

    # update any running upgrade records to failed
    upgrade_records.update_one({'state': 'running'}, {'$set': {'state': 'failed'}})
    updated_record = upgrade_records.update_one({'id': id}, {'$set': record}, True)
    return record

def record_component(id, name, outcome, from_version=None, to_version=None,
                     reference=None):
    """Per-component outcome, alongside the free-text step log.

    The step log says "updating backend server" and then, whatever happened,
    "backend server updated" - so a container that failed its smoke check and
    was rolled back reads the same as one that upgraded cleanly. During an
    incident the question is which containers actually moved and to what, and
    that could not be answered from the record at all.

    pinned says whether the bytes were fixed by digest or resolved from a tag,
    which is the difference between "we know what ran" and "we know what we
    asked for".
    """
    entry = {
        'component': name,
        'outcome': outcome,
        'from': from_version,
        'to': to_version,
        'reference': reference,
        'pinned': bool(reference and '@sha256:' in reference),
        'at': ms_timestamp(),
    }
    upgrade_records.update_one(
        {'id': id},
        {'$pull': {'components': {'component': name}}})
    upgrade_records.update_one(
        {'id': id}, {'$push': {'components': entry}}, True)
    return entry


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
        opts, _ = getopt.getopt(
            argv, "i:t:s:c:",
            ['component=', 'outcome=', 'from=', 'to=', 'ref='])
    except getopt.GetoptError:
        sys.exit(2)

    id        = None
    text      = None
    num_steps = None
    cur_step  = None
    component = None
    outcome   = None
    from_v    = None
    to_v      = None
    reference = None
    for opt, arg in opts:
        if opt == '-i':
            id = arg

        elif opt == '-t':
            text = arg

        elif opt == '-s':
            num_steps = int(arg)
        
        elif opt == '-c':
            cur_step = int(arg)

        elif opt == '--component':
            component = arg
        elif opt == '--outcome':
            outcome = arg
        elif opt == '--from':
            from_v = arg
        elif opt == '--to':
            to_v = arg
        elif opt == '--ref':
            reference = arg

    if not id:
        return

    if component:
        record_component(id, component, outcome or 'unknown',
                         from_version=from_v, to_version=to_v,
                         reference=reference)
        return

    record = get_record(id)
    if not record:
        record = initialize(id, num_steps)
    else:
        update(record, cur_step, text)


if __name__ == "__main__":
    main(sys.argv[1:])
