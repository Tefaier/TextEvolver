from flask import render_template, redirect, url_for, request, flash, send_file
from web_app import app
from web_app.forms import LoginForm, RegisterForm, SearchForm
from flask_login import current_user, login_user, logout_user, login_required
from io import BytesIO
from zipfile import ZipFile
from web_app.server_functions import *


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/download_files', methods=['POST'])
@login_required
def download_files():  # get files (guarantee) make a zip and send
    archive = BytesIO()
    try:
        with ZipFile(archive, 'w') as zipf:
            for file in os.listdir(work_directory + str(current_user.id) + new_dir):
                zipf.write(work_directory + str(current_user.id) + new_dir + '/' + file, 'Your files/' + file)
                os.remove(work_directory + str(current_user.id) + new_dir + '/' + file)
        if get_user_process(current_user.id) == [0, 0]:
            shutil.rmtree(work_directory + str(current_user.id))
        archive.seek(0)
        return send_file(archive, mimetype='zip', as_attachment=True, download_name='Files.zip')
    except FileNotFoundError:
        return redirect(request.referrer)


@app.route('/terminate', methods=['POST'])
@login_required
def terminate():  # terminate process
    terminate_process(current_user.id)
    shutil.rmtree(work_directory + str(current_user.id) + origin_dir)
    os.mkdir(work_directory + str(current_user.id) + origin_dir)
    return redirect(request.referrer)


@app.route('/delete_set/<int:id>', methods=['POST'])
@login_required
def delete_set(id):  # delete setting
    setting = db.session.query(Setting).filter(Setting.owner_id == current_user.id, Setting.id == id).first()
    db.session.delete(setting)
    db.session.commit()
    return "nothing"


@app.route('/add_set', methods=['POST'])
@login_required
def add_set():  # add setting
    if len(list(current_user.settings)) < current_user.set_limit:
        create_setting(current_user.id)
    else:
        flash('Reached limit of settings number allowed -> setting not added', 'error')
    return redirect(url_for('my_settings'))


@app.route('/search/<string:phrase>/<int:page>', methods=['GET', 'POST'])
@login_required
def search(phrase, page):  # search
    form = SearchForm()
    return render_template('search.html', form=form, user=current_user, files_situation=get_user_process(current_user.id),
                           results=get_search_results(phrase, page, results_on_page))


@app.route('/search_req', methods=['POST'])
@login_required
def search_req():  # search
    form = SearchForm(request.form)
    if request.method == 'POST' and form.validate():
        return redirect(url_for('search', phrase=form.search.data, page=1))


@app.route('/my_settings', methods=['GET', 'POST'])
@login_required
def my_settings():  # show user's settings\
    form = SearchForm()
    return render_template('all_settings.html', form=form, user=current_user, files_situation=get_user_process(current_user.id), limit=current_user.set_limit)


@app.route('/setting/<int:id>', methods=['GET', 'POST'])
@login_required
def setting(id):  # setting - MAKE
    setting = db.session.query(Setting).get(id)
    form = SearchForm()
    if request.method == 'POST':
        if ('copy' in request.form) and setting.owner_id != current_user.id:
            if len(list(current_user.settings)) < current_user.set_limit:
                create_setting(current_user.id, id)
            else:
                flash('Reached limit of settings number allowed -> setting not added', 'error')
            return redirect(url_for('my_settings'))
        elif ('save' in request.form) and setting.owner_id == current_user.id:
            results = collect_values(id, request.form, request.files)
            if results == False:
                return render_template('setting.html', user=current_user, setting=setting, form=form,
                                       files_situation=get_user_process(current_user.id),
                                       allowed_extensions=allowed_extensions)
            else:
                reset_values(id, results[0], results[1], results[2], results[3], results[4], results[5], results[6], results[7], results[8], results[9])
                flash(f'Setting change successful with name "{results[0]}"', 'success')
                return redirect(url_for('my_settings'))
        elif ('run' in request.form) and get_user_process(current_user.id) == [0, 0]:
            set_files_for_process(current_user.id, request.files.getlist('Process_files'))
            if get_user_process(current_user.id) != [0, 0]:
                initiate_process(current_user.id, id)
            return redirect(url_for('my_settings'))
        elif ('save_run' in request.form) and setting.owner_id == current_user.id and get_user_process(current_user.id) == [0, 0]:
            results = collect_values(id, request.form, request.files)
            if results == False:
                return render_template('setting.html', user=current_user, setting=setting, form=form,
                                       files_situation=get_user_process(current_user.id),
                                       allowed_extensions=allowed_extensions)
            else:
                reset_values(id, results[0], results[1], results[2], results[3], results[4], results[5], results[6], results[7], results[8], results[9])
            set_files_for_process(current_user.id, request.files.getlist('Process_files'))
            if get_user_process(current_user.id) != [0, 0]:
                initiate_process(current_user.id, id)
            return redirect(url_for('my_settings'))
    return render_template('setting.html', user=current_user, setting=setting, form=form,
                           files_situation=get_user_process(current_user.id), allowed_extensions=allowed_extensions)


@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():  # enter
    if current_user.is_authenticated:
        return redirect(url_for('my_settings'))
    form = LoginForm(request.form)
    if request.method == 'POST' and request.form['subm'] == 'Sign In':
        # checking if it is a class
        user = db.session.query(User).filter_by(name=form.username.data).first()
        if user is None or not user.password == form.password.data or not form.validate():
            check_logreg(form.username.data, form.password.data)
            if user is None or not user.password == form.password.data:
                flash("User with these username and password doesn't exist", 'error')
            return render_template('loging.html', form=form)
        login_user(user)
        flash(f'You successfully logged into account {user.name}', 'success')
        return redirect(url_for('my_settings'))
    return render_template('loging.html', form=form)


@app.route('/register', methods=['GET', 'POST'])
def register():  # register
    if current_user.is_authenticated:
        return redirect(url_for('my_settings'))
    form = RegisterForm(request.form)
    if request.method == 'POST' and request.form['reg'] == 'Register':
        user = db.session.query(User).filter_by(name=form.username.data).first()
        if not ((form.validate()) and (user is None)):
            check_logreg(form.username.data, form.password.data, form.confirm.data)
            return render_template('register.html', form=form)
        user = create_user(form.username.data, form.password.data)
        login_user(user)
        flash(f'You successfully registered account {user.name}', 'success')
        return redirect(url_for('my_settings'))
    return render_template('register.html', form=form)
