"""MLP classifier + training loop for optional PyTorch models."""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn


class MLPClassifier(nn.Module):
    def __init__(self, input_dim: int, hidden_sizes: list[int], dropout: float, num_classes: int):
        super().__init__()
        layers = []
        prev = input_dim
        for size in hidden_sizes:
            layers.append(nn.Linear(prev, size))
            layers.append(nn.ReLU())
            layers.append(nn.Dropout(dropout))
            prev = size
        layers.append(nn.Linear(prev, num_classes))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)

    def predict_proba(self, x: torch.Tensor) -> np.ndarray:
        self.eval()
        with torch.no_grad():
            logits = self.net(x)
            return torch.softmax(logits, dim=1).cpu().numpy()


def fit_mlp(model, X, y, Xv, yv, device: str, cfg: dict) -> dict:
    model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(cfg.get("learning_rate", 0.001)))
    loss_fn = nn.CrossEntropyLoss()
    batch_size = int(cfg.get("batch_size", 1024))
    epochs = int(cfg.get("epochs", 30))
    n = len(X)

    X, y = X.to(device), y.to(device)
    Xv, yv = Xv.to(device), yv.to(device)
    history = {"train_loss": [], "val_loss": [], "val_acc": []}
    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(n)
        total_loss = 0.0
        steps = 0
        for start in range(0, n, batch_size):
            idx = perm[start : start + batch_size]
            batch_x, batch_y = X[idx], y[idx]
            optimizer.zero_grad()
            logits = model(batch_x)
            loss = loss_fn(logits, batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            steps += 1
        model.eval()
        with torch.no_grad():
            vlogits = model(Xv)
            vloss = loss_fn(vlogits, yv)
            vacc = (vlogits.argmax(1) == yv).float().mean().item()
        history["train_loss"].append(total_loss / max(steps, 1))
        history["val_loss"].append(vloss.item())
        history["val_acc"].append(vacc)
    return history
