---
layout: feed_item
title: "A robust deep learning framework for predicting carbon dioxide-water alternating gas injection performance and optimization"
date: 2026-01-15 00:00:00 +0000
categories: [research_papers]
tags: ['urgent']
keywords: ['deep', 'urgent', 'robust', 'learning']
description: "Carbon dioxide (CO2) emissions pose a major environmental concern, and various methods are used for CO2 sequestration"
external_url: https://www.frontiersin.org/articles/10.3389/fclim.2025.1710187
is_feed: true
source_feed: "Frontiers in Climate | New and Recent Articles"
feed_category: "research_papers"
---

Carbon dioxide (CO2) emissions pose a major environmental concern, and various methods are used for CO2 sequestration. CO2-water activating gas (CO2-WAG) injection is a technique used to increase production of oil and sequester CO2 in subsurface formations. However, the performance of the CO2-WAG project depends on various parameters, such as injection rates, cycle size, and ratio, that traditionally require numerous computationally expensive simulations. The study introduces a robust machine learning workflow for CO2-WAG performance prediction and optimization by using a model calibrated using Bell Creek formation properties. Machine learning models are based on algorithms like extreme gradient boosting (XGBoost), linear regression (LR), random forest (RF), k-nearest neighbor (KNN), support vector regression (SVR), artificial neural network (ANN), convolutional neural network (CNN), and hybrid models such as ANN and CNN coupled with XGBoost (ANN-XGBoost, and CNN-XGBoost) to predict CO2-WAG performance. A dataset of 2,400 samples was generated using the CMG-GEM numerical simulator, incorporating seven input parameters (e.g., injection rate, CO2-WAG cycle size, and WAG ratio) and three output parameters, with 80% of the dataset allocated for training and 20% for validation and testing. Among the proposed models, the hybrid model ANN-XGBoost demonstrated superior performance, accurately predicting total oil production, CO2 storage, and efficiency, with high R2 scores of 0.99159, 0.97515, and 0.98706, and corresponding lower RMSE values of 2.8 × 10−2, 1.5 × 10−1, and 2.4 × 10−2. Coupling the proxy with particle swarm optimization (PSO) yielded 12.8% increase in cumulative oil production and 11% increase in CO2 storage. Furthermore, in terms of speed, the projected workflow requires less minutes to complete predictions and optimization, while traditional numerical simulators require 4–5 min per scenario. These findings validates the robustness and computational efficiency of the proposed machine learning workflow for predicting CO2-WAG performance and optimization.

[Read original article](https://www.frontiersin.org/articles/10.3389/fclim.2025.1710187)
