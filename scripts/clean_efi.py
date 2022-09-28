import os
import uuid

def move_efi_imgs():
    path = '/boot/efi/flexible_vision'
    if os.path.exists(path):
        uid = str(uuid.uuid4())
        new_path = path + '_' + uid
        #change folder name
        os.system("mv {} {}".format(path, new_path))
        #move folder
        os.system("mv {} /home/visioncell/".format(new_path))
        home_path = '/home/visioncell/flexible_vision_{}'.format(uid)
        os.system("chmod 777 {}".format(home_path))
    else:
        print('no images in efi - state clean')


def if __name__ == "__main__":
    move_efi_imgs()