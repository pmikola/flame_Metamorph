import os
import random
import sys
from os import path
import numpy as np
import torch
from torch import nn

from discriminator import Metamorph_discriminator
from model import Metamorph
from parameterReinforcer import Metamorph_parameterReinforcer
from teacher import teacher
from flameEngine import flame as fl
from CustomLoss import CustomLoss

# Start ................
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
# os.environ['CUDA_LAUNCH_BLOCKING'] = '1'
# torch.autograd.set_detect_anomaly(True) # Note : Tremendously slowing down program - Attention: Be careful!

no_frame_samples = 50
batch_size = 256
input_window_size = 7

no_frames = 1000
first_frame, last_frame, frame_skip = 0, no_frames, 10
models = []

for i in range(4):
    torch.manual_seed(2024 + i)
    np.random.seed(2024 + i)
    random.seed(2024 + i)
    models.append(Metamorph(no_frame_samples, batch_size, input_window_size, device).to(device))

no_layers = 0
for (name, param) in models[0].named_parameters():
    no_layers += 1
discriminator = Metamorph_discriminator(no_frame_samples, batch_size, input_window_size, device).to(device)
parameterReinforcer = Metamorph_parameterReinforcer(no_layers, 10, 100, 20, 128, input_window_size, device).to(device)

model_params = sum(p.numel() for p in models[0].parameters() if p.requires_grad)
disc_params = sum(p.numel() for p in discriminator.parameters() if p.requires_grad)
reinforcer_params = sum(p.numel() for p in parameterReinforcer.parameters() if p.requires_grad)

print('Metamorph number of parameters', round(model_params * 1e-6, 2), 'M')
print('Discriminator number of parameters', round(disc_params * 1e-6, 2), 'M')
print('Reinforcer number of parameters', round(reinforcer_params * 1e-6, 2), 'M')

t = teacher(models, discriminator, parameterReinforcer, device)
t.fsim = fl.flame_sim(no_frames=no_frames, frame_skip=frame_skip)

OT_backend = 'tensorized'
criterion_model = CustomLoss(device)
criterion_e0 = CustomLoss(device)
criterion_e1 = CustomLoss(device)
criterion_e2 = CustomLoss(device)
criterion_disc = nn.BCELoss(reduction='mean')
criterion_RL = nn.MSELoss(reduction='mean')
criterion = criterion_model, criterion_e0, criterion_e1, criterion_e2, criterion_disc, criterion_RL
optimizer = torch.optim.Adam([
    {'params': t.model.parameters()},
    {'params': t.expert_0.parameters()},
    {'params': t.expert_1.parameters()},
    {'params': t.expert_2.parameters()}
], lr=1e-2, betas=(0.9, 0.999), eps=1e-08, weight_decay=1e-6, amsgrad=False)
disc_optimizer = torch.optim.Adam(t.discriminator.parameters(), lr=5e-4, betas=(0.9, 0.999), eps=1e-08,
                                  weight_decay=1e-6, amsgrad=False)
RL_optimizer = torch.optim.Adam(t.parameterReinforcer.parameters(), lr=5e-3, betas=(0.9, 0.999), eps=1e-08,
                                weight_decay=1e-6, amsgrad=False)

# torch.autograd.set_detect_anomaly(True)
# Note: Eon > Era > Period > Epoch
no_periods = 1
t.no_of_periods = no_periods
# model.load_state_dict(torch.load('model.pt'Ś))
for period in range(1, no_periods + 1):
    t.period = period
    t.fsim = fl.flame_sim(no_frames=1000, frame_skip=frame_skip)
    t.fsim.igni_time = no_frames
    # t.generate_structure()
    t.fsim.fuel_dens_modifier = 1 / t.fsim.dt
    t.fsim.simulate(simulate=0, save_rgb=1, save_alpha=1, save_fuel=1, delete_data=0)
    t.learning_phase(t, no_frame_samples, batch_size, input_window_size, first_frame,
                     last_frame, frame_skip * 2, criterion, optimizer, disc_optimizer, RL_optimizer, device, learning=1,
                     num_epochs=1000)
    # t.fsim.simulate(simulate=0,delete_data=1)

t.visualize_lerning()
t.examine(criterion, device, plot=1)
