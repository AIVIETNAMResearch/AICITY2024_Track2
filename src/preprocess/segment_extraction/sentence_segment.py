from pathlib import Path
from glob import glob
import re
from tqdm import tqdm
from typing import List
import os
import random
from os.path import join as osp
import json
import statistics
import requests
from constant import *

import torch
import torch.nn.functional as F
from torch import Tensor
from transformers import AutoModel, AutoConfig, AutoTokenizer, AutoModelForCausalLM
from vllm import LLM, SamplingParams
import numpy as np
from torcheval.metrics.functional.text import bleu_score

import re
import spacy
from sentence_transformers import SentenceTransformer

class LLMSegment:
    def __init__(
        self,
        model_name='Qwen/Qwen1.5-7B-Chat',
    ):
        # self.model = AutoModelForCausalLM.from_pretrained(
        #     model_name,
        #     device_map='auto',
        #     torch_dtype=torch.bfloat16, 
        #     attn_implementation="flash_attention_2",
        # )
        self.model_name = model_name
        self.ollama = ollama
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        # self.model_device = self.model.device
        if not ollama:
            self.sampling_params = SamplingParams(temperature=0.7, top_p=0.85, top_k=3, max_tokens=512)
            LLM(
                model=model_name,
                #kv_cache_dtype="fp8_e5m2",
                #gpu_memory_utilization=1,
                #  quantization="awq",
                #  dtype="auto",
                #  swap_space=8,
                #  gpu_memory_utilization=0.9,
                #  max_num_seqs=3,
                # dtype='bfloat16'
                # max_model_len=10000,
            )
        
    # def __call__(self, file_path, output_path):
    #     os.makedirs(output_path, exist_ok=True)
    #     file_paths = glob(osp(PROJECT_ROOT, file_path))
    #     for path in tqdm(file_paths):
    #         video_name = path.split('/')[-1].split('_')[0]
    #         with open(path, 'r') as f:
    #             phases = json.load(f)["event_phase"]
    #         for phase in phases:
    #             phase['pesdes_info'] = self.get_information(phase['caption_pedestrian'], input_type='pedes')
    #             phase['vehicle_info'] = self.get_information(phase['caption_vehicle'], input_type='vehicle')
    #         with open(osp(output_path, f'{video_name}_information.json'), 'w') as f:
    #             json.dump(phases, f, indent=4)
    #     return None
    
    def __call__(self, file_path, output_path, run_percentage=1, shuffle=False):
        os.makedirs(output_path, exist_ok=True)
        file_paths = glob(osp(PROJECT_ROOT, file_path))
        if shuffle:
            random.shuffle(file_paths)
        num_run_sample = int(len(file_paths)*run_percentage)
        for path in tqdm(file_paths[:num_run_sample]):
            video_name = path.split('/')[-1].split('_')[0]
            with open(path, 'r') as f:
                phases = json.load(f)["event_phase"]
            pedes_captions = []
            vehicle_captions = []
            for phase in phases:
                pedes_captions.append(self.get_prompt(phase['caption_pedestrian'], input_type='pedes'))
                vehicle_captions.append(self.get_prompt(phase['caption_vehicle'], input_type='vehicle'))
            pedes_informations = self.get_information_vllm(pedes_captions)
            vehicle_informations = self.get_information_vllm(vehicle_captions)
            for phase, pedes_information, vehicle_information in zip(phases, pedes_informations, vehicle_informations):
                phase['pesdes_info'] = pedes_information
                phase['vehicle_info'] = vehicle_information
            with open(osp(output_path, f'{video_name}_information.json'), 'w') as f:
                json.dump(phases, f, indent=4)
        return None
    
    def get_information_vllm(self, texts):
        outputs = self.model.generate(texts, self.sampling_params, use_tqdm=False)
        results = []
        for output in outputs:
            generated_text = output.outputs[0].text
            results.append(generated_text)
        return results
        
    def get_information(self, text, input_type, max_length=512):
        prompt = self.get_prompt(text, input_type)
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model_device)
        outputs = self.model.generate(**inputs, max_new_tokens=max_length)
        output = self.tokenizer.decode(outputs[0][len(inputs[0]):], skip_special_tokens=True)
        
        return output
    
    def ollama_generate(self, prompts):
        result = []
        for prompt in prompts:
            query_data = {
                "model": self.model_name,
                "prompt": prompt,
                "stream": False
            }
            result.append(requests.post(self.url, json=query_data, headers=self.headers).json()['response'])
        return result
    
    @staticmethod
    def information_spliter(file_paths, output_path, error_folder=None):
        '''
        This function used to process the output when calling the __call__ function
        to handle the paragraph and generate infomration about appearance, location,... etc
        the information will be added into the file
        '''
        folder_paths = glob(file_paths)
        pedes_keys_name = ['appearance', 'location', 'environment', 'attention']
        vehicle_keys_name = ['appearance', 'location', 'environment', 'action']
        for path in folder_paths:
            video_name = path.split('/')[-1].split('_')[0]
            error_result = []
            with open(path, 'r') as f:
                phases = json.load(f)
            for phase in phases:
                pesdes_info = phase['pesdes_info'].split('[INST]')[0].split('\n')[:4]
                vehicle_info = phase['vehicle_info'].split('[INST]')[0].split('\n')[:4]
                try:
                    pedes_result = {
                        key:' '.join(value.split(':')[1:]).strip() for (value, key) in zip(pesdes_info, pedes_keys_name[:len(pesdes_info)])
                    }
                    phase['pedes_detail_extraction'] = pedes_result
                except:
                    error_result.append({
                        'pesdes_info': phase['pesdes_info'],
                        'pesdes_caption': phase['caption_pedestrian']
                    })
                if len(pedes_result) < 4:
                    error_result.append({
                        'pesdes_info': phase['pesdes_info'],
                        'pesdes_caption': phase['caption_pedestrian']
                    })
                try:
                    vehicle_result = {
                        key:' '.join(value.split(':')[1:]).strip() for (value, key) in zip(vehicle_info, vehicle_keys_name[:len(vehicle_info)])
                    }
                    phase['vehicle_detail_extract'] = vehicle_result
                except:
                    error_result.append({
                        'vehicle_info': phase['vehicle_info'],
                        'vehicle_caption': phase['caption_vehicle']
                    })
                if len(vehicle_result) < 4:
                    error_result.append({
                        'vehicle_info': phase['vehicle_info'],
                        'vehicle_caption': phase['caption_vehicle']
                    })
            os.makedirs(output_path, exist_ok=True)
            with open(osp(output_path, f'{video_name}_information.json'), 'w') as f:
                json.dump(phases, f, indent=4)
            if (error_result != []) and (error_folder != None):
                os.makedirs(error_folder, exist_ok=True)
                with open(osp(error_folder, f'{video_name}.jsonl'), 'w') as f:
                    json.dump(error_result, f, indent=4)
                    
    @staticmethod
    def check_acc(processed_file_paths='/home/server1-ailab/Desktop/Khai/CVPRW/segmentation_data/mistral/train/processed/*.json'):
        '''
        This function mesure the bleu score between the extracted information
        and the original caption to see if the extracted in formation enough
        or being changed or not
        
        processed_file_paths: glob files paths of processed data return by information_spliter
        functionh
        
        return: bleu score of the extracted information
        '''
        processed_data_paths = glob(processed_file_paths)
        vehicle_total_score = []
        pedes_total_score = []
        for process_file in processed_data_paths:
            with open(process_file, 'r') as f:
                process_phases = json.load(f)
            for phase in process_phases:
                pedes_caption = phase['caption_pedestrian']
                vehicle_caption = phase['caption_vehicle']
                process_pedes_caption = ' '.join([item.strip() for item in phase['pedes_detail_extraction'].values()])
                process_vehicle_caption = ' '.join([item.strip() for item in phase['vehicle_detail_extract'].values()])
                vehicle_total_score.append(bleu_score([process_pedes_caption], [[pedes_caption]], n_gram=3).item())
                pedes_total_score.append(bleu_score([process_vehicle_caption], [[vehicle_caption]], n_gram=3).item())
        print(f'bleu scorer of pedestrian: {statistics.mean(pedes_total_score)}')
        print(f'bleu score of vehicle: {statistics.mean(vehicle_total_score)}')
        
        result = {
            'pedes score': statistics.mean(vehicle_total_score),
            'vehicle score': statistics.mean(vehicle_total_score),
        }
        
        return result
    
    def get_prompt(self, text, input_type):
        return self.get_instruction_promt_wo_system_prompt(text=text, input_type=input_type)    
    
    def get_instruction_promt_wo_system_prompt(self, text, input_type):
        if input_type == 'pedes':
            system_prompt = f'''I will tip you 500k$ for a better solution. You are a helpful system and always generate useful answers. You MUST go straight to the answer and anwser shortly. Extract these 4 information from the paragraph. Each information could be in seperate place in the paragraph, you MUST use linking words to combine these information but you will be penalized if you paraphrase the information in the given paragraph. If the relevant information not containt in the graph return NaN. Give answer with the following format. You will be penalized if answer the wrong format from the above format:
Paragraph: document
Answer:
- appearance: out-look of the pedestrian.
- location: location of the pedestrian.
- environment: the describe of the environment include weather, lightm surface, road,...
- attention: what the pedestrian looking at ?. Information help answer whether the pedestrian can aware of the vehicle

'''
            fewshot_ins_1 = f'''Paragraph: The pedestrian, a female in her 40s, stands diagonally to the right and in front of the vehicle, with her body oriented perpendicular to the vehicle and to the left. She is closely watching the passing vehicle, unaware of its presence. As she prepares to cross the road, she wears a purple T-shirt on her upper body and black slacks on her lower body. Standing on the sidewalk of an urban area, this situation occurs on a bright and clear weekday, with dry and level asphalt as the road surface. The road is a main road with one-way traffic and one lane. Despite the usual traffic volume, the pedestrian remains unaware of the vehicle due to her line of sight being occupied by the passing vehicle.
            Answer:'''
            few_shot_answer_1 = f'''- appearance: The pedestrian, a female in her 40s, she wears a purple T-shirt on her upper body and black slacks on her lower body.
- location: She stands diagonally to the right and in front of the vehicle, with her body oriented perpendicular to the vehicle and to the left. Standing on the sidewalk of an urban area.
- environment: The road is a main road with one-way traffic and one lane.  This situation occurs on a bright and clear weekday, with dry and level asphalt as the road surface
- attention: She is closely watching the passing vehicle, unaware of its presence. Despite the usual traffic volume, the pedestrian remains unaware of the vehicle due to her line of sight being occupied by the passing vehicle.'''
            fewshot_ins_2 = f'''Paragraph: A man in his 30s, with a height of 160 cm, dressed in a gray T-shirt and black shorts, is standing still diagonally to the right in front of a vehicle on a bright, cloudy weekday. The vehicle is traveling on a dry, level asphalt road, part of a main road with one-way traffic and three lanes. The pedestrian's body is oriented in the same direction as the vehicle, with his line of sight focused ahead in the direction of travel. Despite being far from the vehicle, he is closely watching and is almost aware of its presence. The surroundings are urban, with both sides of the road having sidewalks. The pedestrian seems to be waiting or observing something, possibly getting ready to cross the road. The traffic volume is usual, and the road surface conditions are favorable for safe movement. Overall, it appears to be a calm and routine situation in which the pedestrian and the vehicle are momentarily sharing the road space.
            Answer:'''
            fews_shot_answer_2 = f'''- appearance: A man in his 30s, with a height of 160 cm, dressed in a gray T-shirt and black shorts
- location: He is standing still diagonally to the right in front of a vehicle on a bright, cloudy weekday. The pedestrian's body is oriented in the same direction as the vehicle, with his line of sight focused ahead in the direction of travel
- environment: The vehicle is traveling on a dry, level asphalt road, part of a main road with one-way traffic and three lanes. The surroundings are urban, with both sides of the road having sidewalks. The traffic volume is usual, and the road surface conditions are favorable for safe movement
- attention: Despite being far from the vehicle, he is closely watching and is almost aware of its presence. The pedestrian seems to be waiting or observing something'''
            fewshot_ins_3 = f'''Paragraph: A male pedestrian in his twenties, measuring 180 cm in height, was spotted near a vehicle. He was wearing a black jacket on his upper body and black slacks on his lower body. The pedestrian was located in an urban area, specifically on a main road with three lanes for one-way traffic. It was a weekday and the weather was clear, resulting in a dim brightness level. The road conditions were dry, with a level incline and an asphalt surface. The traffic volume was light, and both sides of the road had sidewalks. The environment conditions surrounding the pedestrian suggested a relatively peaceful and safe scenario.
            Answer:'''
            fews_shot_answer_3 = f'''- appearance: A male pedestrian in his twenties, measuring 180 cm in height. He was wearing a black jacket on his upper body and black slacks on his lower body
            - location: The pedestrian was spotted near a vehicle. He was located in an urban area, specifically on a main road with three lanes for one-way traffic
            - environment: It was a weekday and the weather was clear, resulting in a dim brightness level. The road conditions were dry, with a level incline and an asphalt surface. The traffic volume was light, and both sides of the road had sidewalks. The environment conditions surrounding the pedestrian suggested a relatively peaceful and safe scenario.
            - attention: Nan'''
            messages = [
                {"role": "user", "content": f'{system_prompt}{fewshot_ins_1}'},
                {"role": "assistant", "content": few_shot_answer_1},
                {"role": "user", "content": f'{system_prompt}{fewshot_ins_2}'},
                {"role": "assistant", "content": fews_shot_answer_2},
                {"role": "user", "content": f'{system_prompt}{fewshot_ins_3}'},
                {"role": "assistant", "content": fews_shot_answer_3},
                {"role": "user", "content": f'''{system_prompt}Paragraph: {text}
            Answer:'''},
            ]
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )
            return prompt
        else:
            system_prompt = f'''I will tip you 500k$ for a better solution. You are a helpful system and always generate useful answers. You MUST go straight to the answer and anwser shortly. Extract these 4 information from the paragraph. Each information could be in seperate place in the paragraph, you MUST use linking words to combine these information but you will be penalized if you paraphrase the information in the given paragraph. If the relevant information not containt in the graph return NaN. Give answer with the following format. You will be penalized if answer the wrong format from the above format:
Paragraph: document
Answer:
- appearance: out-look of the pedestrian.
- location: location of the pedestrian.
- environment: the describe of the environment include weather, lightm surface, road,...
- action: speed of vehivle and the action of the vehicle: direction, go, pass,...

'''
            fewshot_ins_1 = f'''Paragraph: The vehicle was moving at a constant speed of 10km/h. It was positioned behind a pedestrian and was quite far away from them. The vehicle had a clear view of the pedestrian. It was going straight ahead without any change in direction. The environment conditions indicated that the pedestrian was a male in his 30s with a height of 160 cm. He was wearing a gray T-shirt and black short pants. The event took place in an urban area on a weekday. The weather was cloudy but the brightness was bright. The road surface was dry and level, made of asphalt. The traffic volume was usual on the main road that had one-way traffic with three lanes. Sidewalks were present on both sides of the road.
            Answer:'''
            few_shot_answer_1 = f'''- appearance: The environment conditions indicated that the pedestrian was a male in his 30s with a height of 160 cm. He was wearing a gray T-shirt and black short pants.
- location: It was positioned behind a pedestrian and was quite far away from them. The vehicle had a clear view of the pedestrian.
- environment: The event took place in an urban area on a weekday. The weather was cloudy but the brightness was bright. The road surface was dry and level, made of asphalt. The traffic volume was usual on the main road that had one-way traffic with three lanes. Sidewalks were present on both sides of the road.
- action: The vehicle was moving at a constant speed of 10km/h. It was going straight ahead without any change in direction'''
            fewshot_ins_2 = f'''Paragraph: The vehicle is moving at a constant speed of 15 km/h, proceeding straight ahead on a main road. It is diagonally positioned to the left in front of the pedestrian, who is visible within the vehicle's field of view. The vehicle is far from the pedestrian, providing a safe distance. The environment conditions reflect a female pedestrian in her 40s, standing at a height of 160 cm. She is wearing purple T-shirt, black slacks, and glasses. The event is taking place in an urban location, on a clear and bright weekday. The road surface is dry, with a level inclined asphalt. Traffic volume is normal, and the road is classified as a one-way, one lane street with sidewalks on both sides. This concise description provides an overview of the vehicle's state and the surrounding environmental conditions without any additional personal or emotive details.
            Answer:'''
            fews_shot_answer_2 = f'''- appearance: The environment conditions reflect a female pedestrian in her 40s, standing at a height of 160 cm. She is wearing purple T-shirt, black slacks, and glasses
- location: The vehicle proceeding straight ahead on a main road. It is diagonally positioned to the left in front of the pedestrian, who is visible within the vehicle's field of view. The vehicle is far from the pedestrian, providing a safe distance
- environment: The event is taking place in an urban location, on a clear and bright weekday. The road surface is dry, with a level inclined asphalt. Traffic volume is normal, and the road is classified as a one-way, one lane street with sidewalks on both sides
- action: The vehicle is moving at a constant speed of 15 km/h'''
            fewshot_ins_3 = f'''Paragraph: The vehicle is positioned on the right side of a pedestrian. It is relatively close to the pedestrian, and the pedestrian is clearly visible within the vehicle's field of view. The vehicle is being operated in an urban setting on a regular weekday. The environmental conditions indicate that the pedestrian is a male in his 20s, standing at a height of 160 cm. He is wearing a blue jacket and turquoise slacks. The road conditions are favorable, with a dry asphalt surface and bright lighting. The road on which the vehicle is driving is a main road with one-way traffic and two lanes, and there are sidewalks available on both sides.
            Answer:'''
            fews_shot_answer_3 = f'''- appearance: The pedestrian is a male in his 20s, standing at a height of 160 cm. He is wearing a blue jacket and turquoise slacks
- location: The vehicle is positioned on the right side of a pedestrian. It is relatively close to the pedestrian.
- environment: The vehicle is being operated in an urban setting on a regular weekday. The road conditions are favorable, with a dry asphalt surface and bright lighting. The road on which the vehicle is driving is a main road with one-way traffic and two lanes, and there are sidewalks available on both sides.
- action: Nan'''
            messages = [
                {"role": "user", "content": f'{system_prompt}{fewshot_ins_1}'},
                {"role": "assistant", "content": few_shot_answer_1},
                {"role": "user", "content": f'{system_prompt}{fewshot_ins_2}'},
                {"role": "assistant", "content": fews_shot_answer_2},
                {"role": "user", "content": f'{system_prompt}{fewshot_ins_3}'},
                {"role": "assistant", "content": fews_shot_answer_3},
                {"role": "user", "content": f'''{system_prompt}Paragraph: {text}
            Answer:'''},
            ]
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )
            return prompt

    def get_instruction_w_system_promt(self, text, input_type):
        if input_type == 'pedes':
            system_prompt = f'''I will tip you 500k$ for a better solution. You are a helpful system and always generate useful answers. You MUST go straight to the answer and anwser shortly. Extract these 4 information from the paragraph. Each information could be in seperate place in the paragraph, you MUST use linking words to combine these information but you will be penalized if you paraphrase the information in the given paragraph. If the relevant information not containt in the graph just ignore it. Give answer with the following format:
Paragraph: document
Answer:
- appearance: out-look of the pedestrian.
- location: location of the pedestrian.
- environment: the describe of the environment include weather, lightm surface, road,...
- attention: what the pedestrian looking at ?. Information help answer whether the pedestrian can aware of the vehicle

You will be penalized if answer the wrong format from the above format'''
            fewshot_ins_1 = f'''Paragraph: The pedestrian, a female in her 40s, stands diagonally to the right and in front of the vehicle, with her body oriented perpendicular to the vehicle and to the left. She is closely watching the passing vehicle, unaware of its presence. As she prepares to cross the road, she wears a purple T-shirt on her upper body and black slacks on her lower body. Standing on the sidewalk of an urban area, this situation occurs on a bright and clear weekday, with dry and level asphalt as the road surface. The road is a main road with one-way traffic and one lane. Despite the usual traffic volume, the pedestrian remains unaware of the vehicle due to her line of sight being occupied by the passing vehicle.
            Answer:'''
            few_shot_answer_1 = f'''- appearance: The pedestrian, a female in her 40s, she wears a purple T-shirt on her upper body and black slacks on her lower body.
- location: She stands diagonally to the right and in front of the vehicle, with her body oriented perpendicular to the vehicle and to the left. Standing on the sidewalk of an urban area.
- environment: The road is a main road with one-way traffic and one lane.  This situation occurs on a bright and clear weekday, with dry and level asphalt as the road surface
- attention: She is closely watching the passing vehicle, unaware of its presence. Despite the usual traffic volume, the pedestrian remains unaware of the vehicle due to her line of sight being occupied by the passing vehicle.'''
            fewshot_ins_2 = f'''Paragraph: A man in his 30s, with a height of 160 cm, dressed in a gray T-shirt and black shorts, is standing still diagonally to the right in front of a vehicle on a bright, cloudy weekday. The vehicle is traveling on a dry, level asphalt road, part of a main road with one-way traffic and three lanes. The pedestrian's body is oriented in the same direction as the vehicle, with his line of sight focused ahead in the direction of travel. Despite being far from the vehicle, he is closely watching and is almost aware of its presence. The surroundings are urban, with both sides of the road having sidewalks. The pedestrian seems to be waiting or observing something, possibly getting ready to cross the road. The traffic volume is usual, and the road surface conditions are favorable for safe movement. Overall, it appears to be a calm and routine situation in which the pedestrian and the vehicle are momentarily sharing the road space.
            Answer:'''
            fews_shot_answer_2 = f'''- appearance: A man in his 30s, with a height of 160 cm, dressed in a gray T-shirt and black shorts
- location: He is standing still diagonally to the right in front of a vehicle on a bright, cloudy weekday. The pedestrian's body is oriented in the same direction as the vehicle, with his line of sight focused ahead in the direction of travel
- environment: The vehicle is traveling on a dry, level asphalt road, part of a main road with one-way traffic and three lanes. The surroundings are urban, with both sides of the road having sidewalks. The traffic volume is usual, and the road surface conditions are favorable for safe movement
- attention: Despite being far from the vehicle, he is closely watching and is almost aware of its presence. The pedestrian seems to be waiting or observing something'''
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": fewshot_ins_1},
                {"role": "assistant", "content": few_shot_answer_1},
                {"role": "user", "content": fewshot_ins_2},
                {"role": "assistant", "content": fews_shot_answer_2},
                {"role": "user", "content": f'''Paragraph: {text}
            Answer:'''},
            ]
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )
            return prompt
        else:
            system_prompt = f'''I will tip you 500k$ for a better solution. You are a helpful system and always generate useful answers. You MUST go straight to the answer and anwser shortly. Extract these 4 information from the paragraph. Each information could be in seperate place in the paragraph, you MUST use linking words to combine these information but you will be penalized if you paraphrase the information in the given paragraph. If the relevant information not containt in the graph just ignore it. Give answer with the following format:
Paragraph: document
Answer:
- appearance: out-look of the pedestrian.
- location: location of the pedestrian.
- environment: the describe of the environment include weather, lightm surface, road,...
- action: speed of vehivle and the action of the vehicle: direction, go, pass,...

You will be penalized if answer the wrong format from the above format'''
            fewshot_ins_1 = f'''Paragraph: The vehicle was moving at a constant speed of 10km/h. It was positioned behind a pedestrian and was quite far away from them. The vehicle had a clear view of the pedestrian. It was going straight ahead without any change in direction. The environment conditions indicated that the pedestrian was a male in his 30s with a height of 160 cm. He was wearing a gray T-shirt and black short pants. The event took place in an urban area on a weekday. The weather was cloudy but the brightness was bright. The road surface was dry and level, made of asphalt. The traffic volume was usual on the main road that had one-way traffic with three lanes. Sidewalks were present on both sides of the road.
            Answer:'''
            few_shot_answer_1 = f'''- appearance: The environment conditions indicated that the pedestrian was a male in his 30s with a height of 160 cm. He was wearing a gray T-shirt and black short pants.
- location: It was positioned behind a pedestrian and was quite far away from them. The vehicle had a clear view of the pedestrian.
- environment: The event took place in an urban area on a weekday. The weather was cloudy but the brightness was bright. The road surface was dry and level, made of asphalt. The traffic volume was usual on the main road that had one-way traffic with three lanes. Sidewalks were present on both sides of the road.
- action: The vehicle was moving at a constant speed of 10km/h. It was going straight ahead without any change in direction'''
            fewshot_ins_2 = f'''Paragraph: The vehicle is moving at a constant speed of 15 km/h, proceeding straight ahead on a main road. It is diagonally positioned to the left in front of the pedestrian, who is visible within the vehicle's field of view. The vehicle is far from the pedestrian, providing a safe distance. The environment conditions reflect a female pedestrian in her 40s, standing at a height of 160 cm. She is wearing purple T-shirt, black slacks, and glasses. The event is taking place in an urban location, on a clear and bright weekday. The road surface is dry, with a level inclined asphalt. Traffic volume is normal, and the road is classified as a one-way, one lane street with sidewalks on both sides. This concise description provides an overview of the vehicle's state and the surrounding environmental conditions without any additional personal or emotive details.
            Answer:'''
            fews_shot_answer_2 = f'''- appearance: The environment conditions reflect a female pedestrian in her 40s, standing at a height of 160 cm. She is wearing purple T-shirt, black slacks, and glasses
- location: The vehicle proceeding straight ahead on a main road. It is diagonally positioned to the left in front of the pedestrian, who is visible within the vehicle's field of view. The vehicle is far from the pedestrian, providing a safe distance
- environment: The event is taking place in an urban location, on a clear and bright weekday. The road surface is dry, with a level inclined asphalt. Traffic volume is normal, and the road is classified as a one-way, one lane street with sidewalks on both sides
- action: The vehicle is moving at a constant speed of 15 km/h'''
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": fewshot_ins_1},
                {"role": "assistant", "content": few_shot_answer_1},
                {"role": "user", "content": fewshot_ins_2},
                {"role": "assistant", "content": fews_shot_answer_2},
                {"role": "user", "content": f'''Paragraph: {text}
            Answer:'''},
            ]
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )
            return prompt

class DynamicSentenceSegmentation:
    def __init__(self, model_name='sentence-transformers/all-MiniLM-L6-v2'):
        # self.model = AutoModel.from_pretrained(
        #     model_name,
        #     device_map='auto',
        #     torch_dtype=torch.bfloat16, 
        #     attn_implementation="flash_attention_2",
        # )
        self.model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
        self.nlp = spacy.load("en_core_web_sm")
    
    
    def __call__(
        self,
        data_paths = '/home/server1-ailab/Desktop/Khai/CVPRW/segmentation_data/mistral/train/processed_2/*.json',
        output_path='/home/server1-ailab/Desktop/Khai/CVPRW/segmentation_data/mistral/train/post_processed_2'
    ):
        os.makedirs(output_path, exist_ok=True)
        data_paths = glob(data_paths)
        for data_path in tqdm(data_paths[:2]):
            video_name = data_path.split('/')[-1].split('_')[0]
            with open(data_path, 'r') as f:
                phases = json.load(f)
            for phase in phases:
                phase['post_process_pedes_detail_extraction'] = {
                    key: [] for key in phase['pedes_detail_extraction'].keys()
                }
                phase['post_process_vehicle_detail_extraction'] = {
                    key: [] for key in phase['vehicle_detail_extract'].keys()
                }
                pedes_information_embedding_dict = {
                    key: self.get_embedding(value) for (key, value) in phase['pedes_detail_extraction'].items()
                }
                vehicle_information_embedding_dict = {
                    key: self.get_embedding(value) for (key, value) in phase['vehicle_detail_extract'].items()
                }
                pedestrian_chunks = self.spliter(phase['caption_pedestrian'])
                vehicle_chunks = self.spliter(phase['caption_vehicle'])
                for pedestrian_chunk in pedestrian_chunks:
                    pedestrian_chunk_type = self.get_which_type(pedestrian_chunk, pedes_information_embedding_dict)
                    phase['post_process_pedes_detail_extraction'][pedestrian_chunk_type].append(pedestrian_chunk)
                for vehicle_chunk in vehicle_chunks:
                    vehicle_chunk_type = self.get_which_type(vehicle_chunk, vehicle_information_embedding_dict)
                    phase['post_process_vehicle_detail_extraction'][vehicle_chunk_type].append(vehicle_chunk)
            with open(osp(output_path, f'{video_name}_post_process.json'), 'w') as f:
                json.dump(phases, f, indent=4)
                    
                
    def get_embedding(self, text):
        embedding = self.model.encode([text],  device='cuda', convert_to_tensor=True, batch_size=32)
        return embedding
    
    def get_which_type(self, text, embedding_dict):
        text_embedding = self.get_embedding(text)
        similarity_score_dictionary = {
            self.get_similarity_func(text_embedding, caption_embedding):key for key, caption_embedding in embedding_dict.items()
        }
        text_type = similarity_score_dictionary[max(list(similarity_score_dictionary.keys()))]
        return text_type
    
    def spliter(self, sentences):
        '''
        Split a caption into chunk of information that contain only 1 information about
        the target information. It split the sentence by '.'  ',' 'and'. If some chunk
        dont contain subject it will add the subject in the sentence to chunk
        '''
        result = []
        sentences = re.sub('(T|t)he pedestrian(,| ,)', 'The pedestrian', sentences)
        for sentence in self.nlp(sentences).sents:
            subjects, nouns, chunks = self.get_subject_nouns_chunks(sentence)
            main_subject = None
            if subjects:
                main_subject = subjects[0]
            else:
                if nouns:
                    main_subject = nouns[0]
            if main_subject:
                for chunk in chunks:
                    if not self.is_containt_subj(chunk):
                        chunk = [main_subject] + chunk
                    result.append(self.get_string(chunk).capitalize())
            else:
                result.append(sentence.text)
        return result          
    
    def get_subject_nouns_chunks(self, sentence):
        main_subjects = []
        nouns = []
        chunks = [[]]
        for word in sentence:
            if word.dep_ == 'nsubj':
                main_subjects.append(word)
            if word.pos_ == 'PRON' or word.pos_ == 'NOUN':
                nouns.append(word)
            #if word.text == ',' or word.text == 'and':
            if word.text == 'and':
                chunks.append([])
            else:
              chunks[-1].append(word)
            #chunks[-1].append(word)
        return main_subjects, nouns, chunks

    def get_string(self, chunk):
        text = ''
        for word in chunk:
            text += word.text + ' '
        return text.strip()

    def is_containt_subj(self, chunk):
        for word in chunk:
            if word.dep_ == 'nsubj':
                return True
        return False
    
    def get_similarity_func(self, tensorA, tensorB):
        '''
        Only apply (1, d) (1, d) tensor
        '''
        return F.cosine_similarity(tensorA, tensorB).item()
    
if __name__ == "__main__":
    # segment_caption = SimSegment()
    #data_path = '/home/server1-ailab/Desktop/Khai/CVPRW/wts_dataset_zip/annotations/caption/train/*/*/*.json'
    # data_path = '/home/server1-ailab/Desktop/Khai/CVPRW/wts_dataset_zip/external/BDD_PC_5K/annotations/caption/train/*.json'
    # output_path = '/home/server1-ailab/Desktop/Khai/CVPRW/test_external.json'
    # segment_caption(data_path=data_path, output_path=output_path)
    data_path = '/home/server1-ailab/Desktop/Khai/CVPRW/wts_dataset_zip/external/BDD_PC_5K/annotations/caption/val/*.json'
    output_path = '/home/server1-ailab/Desktop/Khai/CVPRW/segmentation_data/mistral/val/raw_2'
    # segment_caption = LLMSegment(model_name='mistralai/Mistral-7B-Instruct-v0.2')
    # segment_caption(file_path=data_path, output_path=output_path, run_percentage=0.03, shuffle=False)
    # LLMSegment.information_spliter(file_paths='/home/server1-ailab/Desktop/Khai/CVPRW/segmentation_data/mistral/val/raw_2/*.json',
    #                                output_path='/home/server1-ailab/Desktop/Khai/CVPRW/segmentation_data/mistral/val/processed_2',
    #                                error_folder='/home/server1-ailab/Desktop/Khai/CVPRW/segmentation_data/mistral/val/error_folder')
    # LLMSegment.check_acc('/home/server1-ailab/Desktop/Khai/CVPRW/segmentation_data/mistral/val/processed_2/*.json')
    dynamic_sengment = DynamicSentenceSegmentation()
    dynamic_sengment(
        data_paths='/home/server1-ailab/Desktop/Khai/CVPRW/segmentation_data/mistral/val/processed_2/*.json',
        output_path='/home/server1-ailab/Desktop/Khai/CVPRW/test'
    )