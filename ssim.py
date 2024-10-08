'''
code modified from
OneSixth (One-sixth) user on github.com :)
https://github.com/VainF/pytorch-msssim/blob/master/pytorch_msssim/ssim.py
'''

import torch
import torch.jit
import torch.nn.functional as F


@torch.jit.script
def create_window(window_size: int, sigma: float, channel: int):
    '''
    Create 1-D gauss kernel
    :param window_size: the size of gauss kernel
    :param sigma: sigma of normal distribution
    :param channel: input channel
    :return: 1D kernel
    '''
    coords = torch.arange(window_size, dtype=torch.float)
    coords -= window_size // 2

    g = torch.exp(-(coords ** 2) / (2 * sigma ** 2))
    g /= g.sum()

    g = g.reshape(1, 1, 1, -1).repeat(channel, 1, 1, 1)
    return g


@torch.jit.script
def _gaussian_filter(x, window_1d, use_padding: bool):
    '''
    Blur input with 1-D kernel
    :param x: batch of tensors to be blured
    :param window_1d: 1-D gauss kernel
    :param use_padding: padding image before conv
    :return: blured tensors
    '''
    C = x.shape[1]
    padding = 0
    if use_padding:
        window_size = window_1d.shape[3]
        padding = window_size // 2
    out = F.conv2d(x, window_1d, stride=1, padding=(0, padding), groups=C)
    out = F.conv2d(out, window_1d.transpose(2, 3), stride=1, padding=(padding, 0), groups=C)
    return out


@torch.jit.script
def ssim(X, Y, window, data_range: float, use_padding: bool=False):
    '''
    Calculate ssim index for X and Y
    :param X: images
    :param Y: images
    :param window: 1-D gauss kernel
    :param data_range: value range of input images. (usually 1.0 or 255)
    :param use_padding: padding image before conv
    :return:
    '''

    K1 = 0.01
    K2 = 0.03
    compensation = 1.0

    C1 = (K1 * data_range) ** 2
    C2 = (K2 * data_range) ** 2

    mu1 = _gaussian_filter(X, window, use_padding)
    mu2 = _gaussian_filter(Y, window, use_padding)
    sigma1_sq = _gaussian_filter(X * X, window, use_padding)
    sigma2_sq = _gaussian_filter(Y * Y, window, use_padding)
    sigma12 = _gaussian_filter(X * Y, window, use_padding)

    mu1_sq = mu1.pow(2)
    mu2_sq = mu2.pow(2)
    mu1_mu2 = mu1 * mu2

    sigma1_sq = compensation * (sigma1_sq - mu1_sq)
    sigma2_sq = compensation * (sigma2_sq - mu2_sq)
    sigma12 = compensation * (sigma12 - mu1_mu2)

    cs_map = (2 * sigma12 + C2) / (sigma1_sq + sigma2_sq + C2)
    # Fixed the issue that the negative value of cs_map caused ms_ssim to output Nan.
    cs_map = F.relu(cs_map)
    ssim_map = ((2 * mu1_mu2 + C1) / (mu1_sq + mu2_sq + C1)) * cs_map

    ssim_val = ssim_map.mean(dim=(1, 2, 3))  # reduce along CHW
    cs = cs_map.mean(dim=(1, 2, 3))

    return ssim_val, cs


@torch.jit.script
def ms_ssim(X, Y, window, data_range: float, weights, use_padding: bool=False, eps: float=1e-8):
    '''
    interface of ms-ssim
    :param X: a batch of images, (N,C,H,W)
    :param Y: a batch of images, (N,C,H,W)
    :param window: 1-D gauss kernel
    :param data_range: value range of input images. (usually 1.0 or 255)
    :param weights: weights for different levels
    :param use_padding: padding image before conv
    :param eps: use for avoid grad nan.
    :return:
    '''
    weights = weights[:, None]

    levels = weights.shape[0]
    vals = []
    for i in range(levels):
        ss, cs = ssim(X, Y, window=window, data_range=data_range, use_padding=use_padding)

        if i < levels-1:
            vals.append(cs)
            X = F.avg_pool2d(X, kernel_size=2, stride=2, ceil_mode=True)
            Y = F.avg_pool2d(Y, kernel_size=2, stride=2, ceil_mode=True)
        else:
            vals.append(ss)

    vals = torch.stack(vals, dim=0)
    # Use for fix a issue. When c = a ** b and a is 0, c.backward() will cause the a.grad become inf.
    vals = vals.clamp_min(eps)
    # The origin ms-ssim op.
    ms_ssim_val = torch.prod(vals[:-1] ** weights[:-1] * vals[-1:] ** weights[-1:], dim=0)
    # The new ms-ssim op. But I don't know which is best.
    # ms_ssim_val = torch.prod(vals ** weights, dim=0)
    # In this file's image training demo. I feel the old ms-ssim more better. So I keep use old ms-ssim op.
    return ms_ssim_val


class SSIM(torch.jit.ScriptModule):
    __constants__ = ['data_range', 'use_padding']

    def __init__(self, window_size=11, window_sigma=1.5, data_range=255., channel=3, use_padding=False):
        '''
        :param window_size: the size of gauss kernel
        :param window_sigma: sigma of normal distribution
        :param data_range: value range of input images. (usually 1.0 or 255)
        :param channel: input channels (default: 3)
        :param use_padding: padding image before conv
        '''
        super().__init__()
        assert window_size % 2 == 1, 'Window size must be odd.'
        window = create_window(window_size, window_sigma, channel)
        self.register_buffer('window', window)
        self.data_range = data_range
        self.use_padding = use_padding

    @torch.jit.script_method
    def forward(self, X, Y):
        r = ssim(X, Y, window=self.window, data_range=self.data_range, use_padding=self.use_padding)
        return r[0]


class MS_SSIM(torch.jit.ScriptModule):
    __constants__ = ['data_range', 'use_padding', 'eps']

    def __init__(self, window_size=11, window_sigma=1.5, data_range=255., channel=3, use_padding=False, weights=None, levels=None, eps=1e-8):
        '''
        class for ms-ssim
        :param window_size: the size of gauss kernel
        :param window_sigma: sigma of normal distribution
        :param data_range: value range of input images. (usually 1.0 or 255)
        :param channel: input channels
        :param use_padding: padding image before conv
        :param weights: weights for different levels. (default [0.0448, 0.2856, 0.3001, 0.2363, 0.1333])
        :param levels: number of downsampling
        :param eps: Use for fix a issue. When c = a ** b and a is 0, c.backward() will cause the a.grad become inf.
        '''
        super().__init__()
        assert window_size % 2 == 1, 'Window size must be odd.'
        self.data_range = data_range
        self.use_padding = use_padding
        self.eps = eps

        window = create_window(window_size, window_sigma, channel)
        self.register_buffer('window', window)

        if weights is None:
            weights = [0.0448, 0.2856, 0.3001, 0.2363, 0.1333]
        weights = torch.tensor(weights, dtype=torch.float)

        if levels is not None:
            weights = weights[:levels]
            weights = weights / weights.sum()
        self.register_buffer('weights', weights)

    @torch.jit.script_method
    def forward(self, X, Y):
        return ms_ssim(X, Y, window=self.window, data_range=self.data_range, weights=self.weights,
                       use_padding=self.use_padding, eps=self.eps)


if __name__ == '__main__':
    print('Simple Test')
    im = torch.randint(0, 255, (5, 3, 256, 256), dtype=torch.float, device='cuda')
    img1 = im / 255
    img2 = img1 * 0.5

    losser = SSIM(data_range=1.).cuda()
    loss = losser(img1, img2).mean()

    losser2 = MS_SSIM(data_range=1.).cuda()
    loss2 = losser2(img1, img2).mean()

    print(loss.item())
    print(loss2.item())


if __name__ == '__main__':
    print('Training Test')
    import cv2
    import torch.optim
    import numpy as np
    import imageio
    import time

    out_test_video = False
    # 最好不要直接输出gif图，会非常大，最好先输出mkv文件后用ffmpeg转换到GIF
    video_use_gif = False

    im = cv2.imread('test_img1.jpg', 1)
    t_im = torch.from_numpy(im).cuda().permute(2, 0, 1).float()[None] / 255.

    if out_test_video:
        if video_use_gif:
            fps = 0.5
            out_wh = (im.shape[1]//2, im.shape[0]//2)
            suffix = '.gif'
        else:
            fps = 5
            out_wh = (im.shape[1], im.shape[0])
            suffix = '.mkv'
        video_last_time = time.perf_counter()
        video = imageio.get_writer('ssim_test'+suffix, fps=fps)

    # 测试ssim
    print('Training SSIM')
    rand_im = torch.randint_like(t_im, 0, 255, dtype=torch.float32) / 255.
    rand_im.requires_grad = True
    optim = torch.optim.Adam([rand_im], 0.003, eps=1e-8)
    losser = SSIM(data_range=1., channel=t_im.shape[1]).cuda()
    ssim_score = 0
    while ssim_score < 0.999:
        optim.zero_grad()
        loss = losser(rand_im, t_im)
        (-loss).sum().backward()
        ssim_score = loss.item()
        optim.step()
        r_im = np.transpose(rand_im.detach().cpu().numpy().clip(0, 1) * 255, [0, 2, 3, 1]).astype(np.uint8)[0]
        r_im = cv2.putText(r_im, 'ssim %f' % ssim_score, (10, 30), cv2.FONT_HERSHEY_PLAIN, 2, (255, 0, 0), 2)

        if out_test_video:
            if time.perf_counter() - video_last_time > 1. / fps:
                video_last_time = time.perf_counter()
                out_frame = cv2.cvtColor(r_im, cv2.COLOR_BGR2RGB)
                out_frame = cv2.resize(out_frame, out_wh, interpolation=cv2.INTER_AREA)
                if isinstance(out_frame, cv2.UMat):
                    out_frame = out_frame.get()
                video.append_data(out_frame)

        cv2.imshow('ssim', r_im)
        cv2.setWindowTitle('ssim', 'ssim %f' % ssim_score)
        cv2.waitKey(1)

    if out_test_video:
        video.close()

    # 测试ms_ssim
    if out_test_video:
        if video_use_gif:
            fps = 0.5
            out_wh = (im.shape[1]//2, im.shape[0]//2)
            suffix = '.gif'
        else:
            fps = 5
            out_wh = (im.shape[1], im.shape[0])
            suffix = '.mkv'
        video_last_time = time.perf_counter()
        video = imageio.get_writer('ms_ssim_test'+suffix, fps=fps)

    print('Training MS_SSIM')
    rand_im = torch.randint_like(t_im, 0, 255, dtype=torch.float32) / 255.
    rand_im.requires_grad = True
    optim = torch.optim.Adam([rand_im], 0.003, eps=1e-8)
    losser = MS_SSIM(data_range=1., channel=t_im.shape[1]).cuda()
    ssim_score = 0
    while ssim_score < 0.999:
        optim.zero_grad()
        loss = losser(rand_im, t_im)
        (-loss).sum().backward()
        ssim_score = loss.item()
        optim.step()
        r_im = np.transpose(rand_im.detach().cpu().numpy().clip(0, 1) * 255, [0, 2, 3, 1]).astype(np.uint8)[0]
        r_im = cv2.putText(r_im, 'ms_ssim %f' % ssim_score, (10, 30), cv2.FONT_HERSHEY_PLAIN, 2, (255, 0, 0), 2)

        if out_test_video:
            if time.perf_counter() - video_last_time > 1. / fps:
                video_last_time = time.perf_counter()
                out_frame = cv2.cvtColor(r_im, cv2.COLOR_BGR2RGB)
                out_frame = cv2.resize(out_frame, out_wh, interpolation=cv2.INTER_AREA)
                if isinstance(out_frame, cv2.UMat):
                    out_frame = out_frame.get()
                video.append_data(out_frame)

        cv2.imshow('ms_ssim', r_im)
        cv2.setWindowTitle('ms_ssim', 'ms_ssim %f' % ssim_score)
        cv2.waitKey(1)

    if out_test_video:
        video.close()


if __name__ == '__main__':
    print('Performance Testing SSIM')
    import time
    s = SSIM(data_range=1.).cuda()

    a = torch.randint(0, 255, size=(20, 3, 256, 256), dtype=torch.float32).cuda() / 255.
    b = a * 0.5
    a.requires_grad = True
    b.requires_grad = True

    start_record = torch.cuda.Event(enable_timing=True)
    end_record = torch.cuda.Event(enable_timing=True)

    start_time = time.perf_counter()
    start_record.record()
    for _ in range(500):
        loss = s(a, b).mean()
        loss.backward()
    end_record.record()
    end_time = time.perf_counter()

    torch.cuda.synchronize()

    print('cuda time', start_record.elapsed_time(end_record))
    print('perf_counter time', end_time-start_time)


if __name__ == '__main__':
    print('Performance Testing MS_SSIM')
    import time
    s = MS_SSIM(data_range=1.).cuda()

    a = torch.randint(0, 255, size=(20, 3, 256, 256), dtype=torch.float32).cuda() / 255.
    b = a * 0.5
    a.requires_grad = True
    b.requires_grad = True

    start_record = torch.cuda.Event(enable_timing=True)
    end_record = torch.cuda.Event(enable_timing=True)

    start_time = time.perf_counter()
    start_record.record()
    for _ in range(500):
        loss = s(a, b).mean()
        loss.backward()
    end_record.record()
    end_time = time.perf_counter()

    torch.cuda.synchronize()

    print('cuda time', start_record.elapsed_time(end_record))
    print('perf_counter time', end_time-start_time)
