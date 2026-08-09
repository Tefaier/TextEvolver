import signal
import sys
import math
import re
import os
import shutil
import time

from flask import flash

import web_app
from web_app.models import *
from psutil import virtual_memory

from text_analysis import erase_symbols_def
from binary_converter import convert_binary
from multiprocessing import Process
from process_manager import files_processing

new_dir = '/new_files'
origin_dir = '/origin_files'
allowed_extensions = ['docx', 'epub', 'html', 'fb2']
work_directory = 'C:/Users/timab/Code/PyCharmProjects/TextEvolver/work_zone/'
results_on_page = 50
set_size_limit = 536870912  # 512 mb
save_memory_limit = 536870912 * 1.5
files_size_limit = 26214400  # 25 mb


def background_processer():  # controls active threads
    with web_app.app.app_context():
        threads = db.session.query(Thread).filter_by(waits=False).all()
        for thread in threads:
            if get_user_process(thread.user_id)[0] != 0:  # it didn't finish yet so have to restart
                thread.waits = True
                db.session.add(thread)
                db.session.commit()
        while True:
            threads = db.session.query(Thread).filter_by(waits=True).all()
            for thread in threads:
                if virtual_memory().free > save_memory_limit:
                    new_process = Process(target=files_processing, args=[thread.setting_id, thread.user_id, work_directory + str(thread.user_id) + origin_dir, new_dir], daemon=True)
                    new_process.start()
                    thread.waits = False
                    thread.ident = new_process.ident
                    db.session.add(thread)
                    db.session.commit()
            time.sleep(10)


def valid_file(file_name):
    return '.' in file_name and file_name.rsplit('.', 1)[1].lower() in allowed_extensions


def initiate_process(user_id, setting_id):  # [num_to_process, num_processed]
    user = db.session.query(User).get(int(user_id))
    thread = list(user.thread)
    prc = get_user_process(user_id)
    if (len(thread) == 0 or thread[0].waits == False) and prc[1] == 0:  # check that there is no active process
        terminate_process(user_id)
        user = db.session.query(User).get(int(user_id))
        new_thread = Thread(user=user, setting_id=setting_id, waits=True)
        db.session.add(new_thread)
        db.session.commit()
    elif prc[0] != 0:
        flash('There are still files to process', 'error')
    else:
        flash("You can't start new process before you download all your finished files", 'error')


def terminate_process(user_id):
    user = db.session.query(User).get(int(user_id))
    thread = list(user.thread)
    if len(thread) != 0:
        if get_user_process(user_id)[0] != 0 and not thread[0].waits:  # check that this process is still running
            os.kill(thread[0].ident, signal.SIGABRT)
        db.session.delete(thread[0])
        db.session.commit()


def text_check(string: str):
    return bool(re.search(fr"({'|'.join(['[' + x + ']' for x in erase_symbols_def])})", string))


def get_search_results(search_name, iteration, num_in_iter):
    founded = list(db.session.query(Setting).filter(Setting.name.contains(search_name), Setting.public == True))
    results = {"phrase": search_name}
    if len(founded) != 0:
        results.update({"founded": len(founded)})
        pages_found = math.ceil(results['founded'] / num_in_iter)
        if iteration < 1:
            results.update({"page": 1})
        elif iteration > pages_found:
            results.update({"page": pages_found})
        else:
            results.update({"page": iteration})
        results.update({"offset": (results["page"] - 1) * num_in_iter})
        if results["page"] == pages_found:  # last shown
            results.update({"settings": founded[results['offset']:]})
            results.update({"left_border": (results["page"] - 1) * num_in_iter + 1})
            results.update({"right_border": len(founded)})
            results.update({"right_available": False})
        else:
            results.update({"settings": founded[results['offset']:(results["page"]) * num_in_iter]})
            results.update({"left_border": (results["page"] - 1) * num_in_iter + 1})
            results.update({"right_border": (results["page"]) * num_in_iter})
            results.update({"right_available": True})
        if results["page"] == 1:
            results.update({"left_available": False})
        else:
            results.update({"left_available": True})
        return results
    else:
        return {"founded": 0, "settings": [], "left_available": False, "right_available": False, "left_border": 0,
                "right_border": 0, "offset": 0, "page": 1, "phrase": search_name}


def check_logreg(username, password, confirm=None):
    if username == '':
        flash('Username is empty', 'error')
    elif len(username) < 4 or len(username) > 62:
        flash('Username is too small or big', 'error')
    if password == '':
        flash('Password is empty', 'error')
    elif len(password) < 4 or len(password) > 62:
        flash('Password is too small or big', 'error')
    if confirm != None:
        if confirm != password:
            flash('Password confirm isn\'t the same as password', 'error')
        if db.session.query(User).filter_by(name=username).first() is not None:
            flash('Username with this name already exists', 'error')


def get_user_process(user_id):
    try:
        num_to_process = len(os.listdir(work_directory + str(user_id) + origin_dir))
    except:
        num_to_process = 0
    try:
        num_processed = len(os.listdir(work_directory + str(user_id) + new_dir))
    except:
        num_processed = 0
    return [num_to_process, num_processed]


def set_files_for_process(user_id, files):
    try:
        shutil.rmtree(work_directory + str(user_id))
    except:
        pass
    os.mkdir(work_directory + str(user_id))
    os.mkdir(work_directory + str(user_id) + origin_dir)
    os.mkdir(work_directory + str(user_id) + new_dir)
    for file in files:
        if valid_file(file.filename):
            file.save(work_directory + str(user_id) + origin_dir + '/' + file.filename)
        else:
            flash(f'Failed to save/process file {file.filename}', 'error')
    files_size = sum(d.stat().st_size for d in os.scandir(work_directory+str(user_id)+origin_dir+'/') if d.is_file())
    if files_size > files_size_limit:
        shutil.rmtree(work_directory + str(user_id))
        flash(f"Your files for processing exceeded limit of total size {int(files_size_limit / 1024)} Kb by {int((files_size - files_size_limit) / 1024)} Kb", 'error')


def check_access(user_id, setting_id):
    setting = db.session.query(Setting).get(int(setting_id))
    if setting.public:
        return True
    elif setting.owner_id == user_id:
        return True
    else:
        return False


def collect_values(setting_id, form, files):
    setting = db.session.query(Setting).get(int(setting_id))
    passed = True
    fandoms = []
    unit_convs = []
    phrase_convs = []
    image_convs = []
    set_size = 0
    name = form.get('set_name')
    if name is None:
        passed = False
        flash("No name of setting detected", 'error')
    elif name == '':
        passed = False
        flash("Name of setting can't be empty", 'error')

    public = form.get('set_public')
    if public is None:
        passed = False
        flash("No publicity of setting detected", 'error')
    else:
        try:
            public = eval(public)
        except:
            passed = False
            flash("Publicity of setting has problems with its value", 'error')

    clean_empty = form.get('set_empty')
    if clean_empty is None:
        passed = False
        flash("No if to clean empty of setting detected", 'error')
    else:
        try:
            clean_empty = eval(clean_empty)
        except:
            passed = False
            flash("If to clean empty of setting has problems with its value", 'error')

    to_utf = form.get('set_utf')
    if to_utf is None:
        passed = False
        flash("No if to convert ot utf of setting detected", 'error')
    else:
        try:
            to_utf = eval(to_utf)
        except:
            passed = False
            flash("If to convert ot utf of setting has problems with its value", 'error')

    coma_used = form.get('set_coma_sep')
    if coma_used is None:
        passed = False
        flash("No use of separator of setting detected", 'error')
    else:
        try:
            coma_used = eval(coma_used)
        except:
            passed = False
            flash("Use of separator of setting has problems with its value", 'error')

    expect_feet = form.get('set_expect_feet')
    if expect_feet is None:
        passed = False
        flash("No expectation of feet of setting detected", 'error')
    else:
        try:
            expect_feet = eval(expect_feet)
        except:
            passed = False
            flash("Expectation of feet of setting has problems with its value", 'error')

    fandom = form.getlist('fandom')
    set_size += sys.getsizeof(fandom)
    fandom_active = form.getlist('fandom_active')
    set_size += sys.getsizeof(fandom_active)
    fandom_separation = form.getlist('fandom_separation')
    set_size += sys.getsizeof(fandom_separation)
    fandom_value_1 = form.getlist('fandom_value_1')
    set_size += sys.getsizeof(fandom_value_1)
    fandom_value_2 = form.getlist('fandom_value_2')
    set_size += sys.getsizeof(fandom_value_2)
    for index in range(0, len(fandom)):  # set of fandoms
        try:
            fandoms.append(Fandom(name=fandom[index], active=eval(fandom_active[index]), separation=int(fandom_separation[index]),
                                  support_value_1=eval(fandom_value_1[index]), support_value_2=eval(fandom_value_2[index]),
                                  setting=setting))
        except TypeError:
            passed = False
            flash(f"Error with values of fandom {fandom[index]}", 'error')
        except IndexError:
            passed = False
            flash("Number of fandom values doesn't correspond", 'error')
        except:
            passed = False
            flash("Unknown error", 'error')
    unit_from = form.getlist('unit_from')
    set_size += sys.getsizeof(unit_from)
    unit_to = form.getlist('unit_to')
    set_size += sys.getsizeof(unit_to)
    unit_convert = form.getlist('unit_convert')
    set_size += sys.getsizeof(unit_convert)
    unit_can = form.getlist('unit_can')
    set_size += sys.getsizeof(unit_can)
    for index in range(0, len(unit_from)):
        try:
            if (text_check(unit_from[index])):
                passed = False
                flash(f"Phrase from: {unit_from[index]}, has special symbols that aren't allowed", 'error')
            else:
                unit_convs.append(UnitConv(phrase_from=unit_from[index], phrase_to=unit_to[index],
                                           convertation=float(unit_convert[index]), can_be_word=eval(unit_can[index]),
                                           setting=setting))
        except TypeError:
            passed = False
            flash(f"Error with values of unit conversion from phrase: {unit_from[index]}", 'error')
        except IndexError:
            passed = False
            flash(f"Number of unit values from phrase: {unit_from[index]}, doesn't correspond", 'error')
        except:
            passed = False
            flash("Unknown error", 'error')
    phrase_from = form.getlist('phrase_from')
    set_size += sys.getsizeof(phrase_from)
    phrase_to = form.getlist('phrase_to')
    set_size += sys.getsizeof(phrase_to)
    phrase_direct = form.getlist('phrase_direct')
    set_size += sys.getsizeof(phrase_direct)
    phrase_mutations = form.getlist('phrase_mutations')
    set_size += sys.getsizeof(phrase_mutations)
    for index in range(0, len(phrase_from)):
        try:
            if (not eval(phrase_direct[index]) and text_check(phrase_from[index])):
                passed = False
                flash(f"Phrase from: {phrase_from[index]}, has special symbols that aren't allowed when change of phrase isn't direct", 'error')
            else:
                phrase_convs.append(PhraseConv(phrase_from=phrase_from[index], phrase_to=phrase_to[index],
                                               mutations=eval(phrase_mutations[index]),
                                               direct=eval(phrase_direct[index]), setting=setting))
        except TypeError:
            passed = False
            flash(f"Error with values of phrase conversion from phrase: {phrase_from[index]}", 'error')
        except IndexError:
            passed = False
            flash(f"Number of phrase values from phrase: {phrase_from[index]}, doesn't correspond", 'error')
        except:
            passed = False
            flash("Unknown error", 'error')
    image_origin_name = form.getlist('image_origin_name')
    image_phrase = form.getlist('image_phrase')
    set_size += sys.getsizeof(image_phrase)
    image_separation = form.getlist('image_separation')
    set_size += sys.getsizeof(image_separation)
    image_expl = form.getlist('image_expl')
    set_size += sys.getsizeof(image_expl)
    image_mutations = form.getlist('image_mutations')
    set_size += sys.getsizeof(image_mutations)
    image_files = files.getlist('image_files')
    image_binaries = []
    binary_string = ''
    file_iter_finished = False
    for file in image_files:
        if file.filename == '':
            if file_iter_finished:  # new image conv started and image is empty
                file_iter_finished = False
            else:  # separator encounter
                file_iter_finished = True
                image_binaries.append(binary_string)
                binary_string = ''
        else:  # some file to write in binary
            file_iter_finished = False
            data = file.read()
            binary_string += ('*' if binary_string != '' else '') + convert_binary(data, "string")
    for index in range(0, len(image_origin_name)):
        try:
            if image_origin_name[index] == "Origin phrase": # it's a new field
                if image_phrase[index] == "Origin phrase": # restricted name
                    passed = False
                    flash("You can't set 'Origin phrase' for image's phrase", 'error')
                elif image_binaries[index] == '':  # no image for image convertion
                    passed = False
                    flash(f"Image adder with phrase: {image_phrase[index]}, was just created without any image", 'error')
                else:
                    set_size += sys.getsizeof(image_binaries[index])
                    image_convs.append(ImageConv(phrase=image_phrase[index], separation=int(image_separation[index]),
                                                 mutations=eval(image_mutations[index]), images=image_binaries[index],
                                                 explanation=image_expl[index], setting=setting))
            else:  # old field
                if image_binaries[index] == '':  # keep old images
                    ash_image = db.session.query(ImageConv).filter_by(phrase=image_origin_name[index]).first()
                    if ash_image is None:
                        flash(f"Image adder with phrase: {image_phrase[index]}, was damaged without recovery", 'error')
                    else:
                        set_size += sys.getsizeof(ash_image.images)
                        image_convs.append(ImageConv(phrase=image_phrase[index], separation=int(image_separation[index]),
                                                     mutations=eval(image_mutations[index]), images=ash_image.images,
                                                     explanation=image_expl[index], setting=setting))
                else:
                    set_size += sys.getsizeof(image_binaries[index])
                    image_convs.append(ImageConv(phrase=image_phrase[index], separation=int(image_separation[index]),
                                                 mutations=eval(image_mutations[index]), images=image_binaries[index],
                                                 explanation=image_expl[index], setting=setting))
        except TypeError:
            passed = False
            flash(f"Error with values of image adder with phrase: {image_phrase[index]}", 'error')
        except IndexError:
            passed = False
            flash(f"Number of image values triggered by phrase: {image_phrase[index]}, doesn't correspond", 'error')
        except:
            passed = False
            flash("Unknown error", 'error')
    if set_size > set_size_limit:
        passed = False
        flash(f"Your setting exceeded limit of setting size {int(set_size_limit / 1024)} Kb by {int((set_size - set_size_limit) / 1024)} Kb", 'error')
    return (name, public, clean_empty, to_utf, coma_used, expect_feet, fandoms, unit_convs, phrase_convs, image_convs) if passed else False