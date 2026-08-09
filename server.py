from multiprocessing import Process

import web_app
from web_app.server_functions import background_processer

# ngrok

if __name__=='__main__':
    background = Process(target=background_processer, daemon=False)
    background.start()
    #web_app.app.run(host='192.168.1.5', port=5000)
    web_app.app.run(port=5000)