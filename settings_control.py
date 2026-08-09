from bs4 import BeautifulSoup
from selenium import webdriver
from web_app.models import db, Setting
import web_app
from web_settings import build_driver


def get_settings(object, id):
    with web_app.app.app_context():
        setting = db.session.query(Setting).get(int(id))
        for fandom in setting.fandoms:
            if fandom.name == "Pokemons":
                object.settings.update({"pokemon": fandom.active})
                if fandom.active:
                    object.settings.update({"show_pokemon_weight": fandom.support_value_1})
                    object.settings.update({"show_pokemon_height": fandom.support_value_2})
                    set_pokemons(object, fandom.separation)
        for unit_conv in setting.unit_convs:
            object.units_list.update({unit_conv.phrase_from: {"split": unit_conv.phrase_to.split(' '),
                                                              "new unit": unit_conv.phrase_to,
                                                              "conversion": unit_conv.convertation,
                                                              "can be word": unit_conv.can_be_word}})
        for image_conv in setting.image_convs:
            binaries = image_conv.images.split('*')
            try:
                element = object.pokemons_list[image_conv.phrase]
                element["separation"] = image_conv.separation
                element["explanation"] = image_conv.explanation
                for binary in binaries:
                    element["binary"].append(binary)
            except:
                object.extra_img_list.update({image_conv.phrase: {"split": image_conv.phrase.split(' '),
                                                                  "separation": image_conv.separation, "last word": None,
                                                                  "mutation": image_conv.mutations, "binary": binaries,
                                                                  "explanation": image_conv.explanation}})
        for phrase_conv in setting.phrase_convs:
            if phrase_conv.direct:
                object.direct_conversions.update({phrase_conv.phrase_from: phrase_conv.phrase_to})
            else:
                object.word_conversions.update({phrase_conv.phrase_from: {"split": phrase_conv.phrase_from.split(' '),
                                                                          "new words": phrase_conv.phrase_to,
                                                                          "mutation": phrase_conv.mutations}})
        object.settings.update({"coma in digits": setting.use_coma_sep})
        object.settings.update({"feet check": setting.expect_feet})
        object.settings.update({"clean empty": setting.clean_empty})
        object.settings.update({"convert to utf": setting.convert_to_utf})
        if setting.expect_feet:
            try:
                object.units_list["feet"]
            except:
                object.units_list.update({'feet': {"split": ['feet'],
                                                   "new unit": 'cm', "conversion": 30.3, "can be word": True}})


def set_pokemons(object, default_separation: int):
    driver = build_driver()
    driver.get(object.pokemons_link + object.pokemons_link_list)
    r = driver.page_source
    soup = BeautifulSoup(r, 'html.parser')
    trs = soup.find('tbody').find_all('tr')
    for tr in trs:
        name_field = tr.find(class_="cell-name")
        link_field = name_field.find('a')
        mute_field = name_field.find(class_='text-muted')
        link = link_field.get('href')
        nickname = link_field.get_text().replace('♀', '').replace('♂', '')
        if nickname not in object.pokemons_list:
            object.pokemons_list.update({nickname: {"split": nickname.split(' '), "separation": default_separation, "link": object.pokemons_link + link, "last word": None, "binary": [], "explanation": None}})
        elif mute_field!=None and (nickname in mute_field.text):
            nickname = mute_field.text.replace('♀', '').replace('♂', '')
            object.pokemons_list.update({nickname: {"split": nickname.split(' '), "separation": default_separation, "link": object.pokemons_link + link, "last word": None, "binary": [], "explanation": None}})
    driver.quit()
