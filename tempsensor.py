import adafruit_dht
from board import D4 as pin
import time 
import datetime

sensor = adafruit_dht.DHT22
# pin = 7
dht_device = adafruit_dht.DHT22(pin)
print("tada")
# humidity, temperature = adafruit_dht.read_retry(sensor, pin)
i = True
while i == True:
    try:
        time.sleep(.5)
        temperature = dht_device.temperature
        humidity = dht_device.humidity
        print(datetime.datetime.now(), temperature,"C ",humidity,"%")
    except RuntimeError:
        print(datetime.datetime.now(), "failed read")
# if humidity is not None and temperature is not None:
#     print('Temp={0:0.1f}*C  Humidity={1:0.1f}%'.format(temperature, humidity))
# else:
#     print('Failed to get reading. Try again!')