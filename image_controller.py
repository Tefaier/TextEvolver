from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
import tkinter as tk
from PIL import Image, ImageTk
import requests
from io import BytesIO
import os
from seleniumbase import Driver

from binary_converter import convert_binary
from web_settings import build_driver


def get_image(binary: any, name: str, text1: str, text2: str):
    try:
        binary = convert_binary(binary, "PIL")
        root = tk.Tk()
        root.geometry("+0+0")
        image = Image.open(BytesIO(binary))
        image_w = image.width
        image_h = image.height
        safe_outlay = 80
        scale_h = 1 if image_h < root.winfo_screenheight() - safe_outlay else (
                    (root.winfo_screenheight() - safe_outlay) / image_h)
        scale_w = 1 if image_w < root.winfo_screenwidth() - safe_outlay else (
                    (root.winfo_screenwidth() - safe_outlay) / image_w)
        scale = min(scale_w, scale_h)
        if scale < 1:
            image = image.resize((int(image_w * scale), int(image_h * scale)))
            image_w = image.width
            image_h = image.height
        image = ImageTk.PhotoImage(image)
        canvas = tk.Canvas(root, width=image_w, height=image_h)
        canvas.create_image(0, 0, anchor=tk.NW, image=image)
        if text1 != "":
            canvas.config(height=image_h + 30)
            text1_id = canvas.create_text(image_w * 0.5, image_h, anchor=tk.N, text=text1, font=('Helvetica', '30'))
        if text2 != "":
            canvas.config(width=image_w + 30)
            text2_id = canvas.create_text(image_w, image_h * 0.5, anchor=tk.W, text=text2, font=('Helvetica', '27'))
            bounds = canvas.bbox(text2_id)
            canvas.coords(text2_id, image_w + (bounds[3] - bounds[1]) * 0.5 - 5, image_h * 0.5)
            canvas.itemconfig(text2_id, anchor=tk.CENTER)
            canvas.itemconfig(text2_id, angle=-90)
        canvas.pack()
        root.update()
        canvas.postscript(file=name + '.eps', colormode='color', pagewidth=canvas.winfo_width() - 1,
                          pageheight=canvas.winfo_height() - 1)
        root.destroy()
        img = Image.open(name + '.eps')
        buffered = BytesIO()
        img.save(buffered, format="JPEG")
        img.close()
        os.remove(name + '.eps')
        return convert_binary(buffered.getvalue(), "string")
    except Exception as e:
        return None


def get_pokemon_image(key: str, settings: dict, link: str, binary = None, text2 = None):
    try:
        name_show = True
        weight_show = settings["show_pokemon_weight"]
        height_show = settings["show_pokemon_height"]
        driver = build_driver()
        driver.get(link)
        try:
            tab = driver.find_element(By.XPATH, "//div[@class='sv-tabs-tab-list']/a[text()='" + key + "']")
            if tab.is_displayed():
                tab.click()
        except NoSuchElementException:
            pass
        link = driver.find_element(By.XPATH, "//div[@class='sv-tabs-panel-list']//following-sibling::div[@class='sv-tabs-panel active']//a").get_attribute('href')
        content = requests.get(link).content
        text1_string = ""
        text2_string = ""
        if name_show:
            text1_string = key
        if text2 == None:
            if height_show:
                tab = driver.find_element(By.XPATH,
                                          "//div[@class='sv-tabs-panel-list']//following-sibling::div[@class='sv-tabs-panel active']//th[text()='Height']//parent::tr//td")
                height = str(tab.text.rsplit('(', 1)[0])
                if (text2_string != ""):
                    text2_string += "  "
                text2_string += height
            if weight_show:
                tab = driver.find_element(By.XPATH,
                                          "//div[@class='sv-tabs-panel-list']//following-sibling::div[@class='sv-tabs-panel active']//th[text()='Weight']//parent::tr//td")
                weight = str(tab.text.rsplit('(', 1)[0])
                if (text2_string != ""):
                    text2_string += "  "
                text2_string += weight
        else:
            text2_string = text2
        driver.quit()
        if binary == None:
            return get_image(content, key, text1_string, text2_string)
        else:
            return get_image(binary, key, text1_string, text2_string)
    except:
        return None

def get_image_binary(path: str):
    try:
        img = Image.open(path)
    except:
        print(f"Failed opening image on {path}")
        return None
    buffered = BytesIO()
    img.save(buffered, format=img.format)
    return convert_binary(buffered.getvalue(), "string")