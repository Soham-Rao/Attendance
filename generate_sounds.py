import wave
import math
import struct
import os

def generate_tone(filename, frequency=440, duration=0.5, volume=0.5, decay=True):
    sample_rate = 44100
    n_samples = int(sample_rate * duration)
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    
    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(1) # Mono
        wav_file.setsampwidth(2) # 2 bytes per sample (16-bit)
        wav_file.setframerate(sample_rate)
        
        for i in range(n_samples):
            t = i / sample_rate
            val = math.sin(2 * math.pi * frequency * t)
            
            # Apply decay if requested
            current_vol = volume
            if decay:
                current_vol = volume * (1 - (i / n_samples))
            
            # Scale to 16-bit integer
            sample = int(val * current_vol * 32767)
            wav_file.writeframes(struct.pack('h', sample))

if __name__ == "__main__":
    static_sounds_dir = os.path.join("static", "sounds")
    
    # Ding: Higher pitch, longer
    generate_tone(os.path.join(static_sounds_dir, "ding.wav"), frequency=800, duration=0.4, volume=0.5)
    
    # Tik: Lower pitch, very short
    generate_tone(os.path.join(static_sounds_dir, "tik.wav"), frequency=400, duration=0.1, volume=0.3)
    
    print("Sound files generated in static/sounds/")
