/*
 * VCP_DMA.c
 * This file is about the VCP functions with the DMA
 *  Created on: Jan 16, 2021
 *      Author: Joaquin
 */


#include "VCP_DMA.h"


//uint8_t sizeTransferredByUsb[64];
//int USART_RX_DATA_SIZE = 64;
//int bufId_To_Dma = 0;


extern uint8_t Data[64];
extern uint8_t USB_Buffer[64];
//static void TransferComplete(DMA_HandleTypeDef *hdma_memtomem_dma2_stream0);

/**
  * @brief  DMA callback set
  * @note   This function is to set the necessary DMA callback
  * @retval None
  */
void CDC_DMA_Callbakset(){

	HAL_DMA_RegisterCallback(&hdma_memtomem_dma2_channel1,HAL_DMA_XFER_CPLT_CB_ID,TransferComplete);

}

/**
  * @brief  CDC DMA main function for sending the data with USB in circular buffer
  * @note   DMAbuffer0 is sent through the USB when the DMAbuffer0 process is completed. Similar for the DMAbuffer1.
  *			After USB has completed the transfer process, the DMAbuffer is swapped.
  * @retval None
  */

void CDC_DMA()
{
	static int lastUSB = 1;
	static int lastDMA = 0;
	int cnt = 0;

	while(lastUSB==Bufferid_USB) {
		if(cnt++ > 0xfff0) break;
		}
//	uint32_t isDebugging = CoreDebug->DHCSR;
//	isDebugging = isDebugging & CoreDebug_DHCSR_C_DEBUGEN_Msk;
//	 && (((CoreDebug->DHCSR)&CoreDebug_DHCSR_C_DEBUGEN_Msk) != CoreDebug_DHCSR_C_DEBUGEN_Msk)
	cnt = 0;
	while(CDC_Transmit_FS(&(USB_Buffer[Bufferid_USB*64]),32) == USBD_BUSY )
		{
		if(cnt++ > 0xfff0) break;
		__NOP();			//USB transfer
		__NOP();			//USB transfer
		__NOP();			//USB transfer
		__NOP();			//USB transfer
		}
	lastUSB = Bufferid_USB;
	cnt  = 0;
	while(lastDMA==Bufferid_DMA) {
		if(cnt++ > 0xfff0) break;
	}
	if (HAL_DMA_Start_IT(&hdma_memtomem_dma2_channel1,(uint32_t)&(Data[Bufferid_DMA*64]),(uint32_t)&(USB_Buffer[Bufferid_DMA*64]),32) != HAL_OK) //DMA transfer
			{Error_Handler();}
	lastDMA = Bufferid_DMA;
}




/**
  * @brief  DMA conversion complete callback
  * @note   This function is executed when the transfer complete interrupt
  *         is generated
  * @retval None
  */
static void TransferComplete(DMA_HandleTypeDef *hdma_memtomem_dma2_stream0)
{
	Bufferid_USB = Bufferid_DMA;
	Bufferid_DMA=1-Bufferid_DMA;
}


