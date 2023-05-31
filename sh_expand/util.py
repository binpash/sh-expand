from datetime import timedelta

import logging

def log(msg: str):
    logging.info(f'Expansion: {msg}')

def print_time_delta(prefix, start_time, end_time):
    ## Always output time in the log.
    time_difference = (end_time - start_time) / timedelta(milliseconds=1)
    ## If output_time flag is set, log the time
    log(f'{prefix} time: {time_difference} ms')
